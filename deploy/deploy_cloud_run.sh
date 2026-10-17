#!/usr/bin/env bash
# Deploy Command Center + Extraction (+ optional Similarity) to Cloud Run.
# suffai-api has been removed — command_center is the BFF.
# Requires: gcloud auth, project set.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GCP_REGION:-asia-south1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Project=$PROJECT Region=$REGION"

gcloud artifacts repositories create suffai \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT" 2>/dev/null || true

CC_IMG="$REGION-docker.pkg.dev/$PROJECT/suffai/suffai-command-center:latest"
WORKER_IMG="$REGION-docker.pkg.dev/$PROJECT/suffai/suffai-extraction:latest"

docker build -f "$ROOT/Dockerfile.worker" -t "$WORKER_IMG" "$ROOT"
docker push "$WORKER_IMG"

gcloud run deploy suffai-extraction \
  --image="$WORKER_IMG" \
  --region="$REGION" \
  --platform=managed \
  --no-allow-unauthenticated \
  --set-env-vars="ENABLE_VERTEX=${ENABLE_VERTEX:-false},GOOGLE_CLOUD_PROJECT=$PROJECT,GCS_IMAGE_BUCKET=${GCS_IMAGE_BUCKET:-${PROJECT}-suffai-garment-images},VERTEX_LOCATION=${VERTEX_LOCATION:-asia-south1}" \
  --project="$PROJECT"

WORKER_URL="$(gcloud run services describe suffai-extraction --region="$REGION" --project="$PROJECT" --format='value(status.url)')"

# Optional: deploy similarity if script present
if [[ -x "$ROOT/deploy/deploy_similarity.sh" ]]; then
  echo "Deploying similarity (deploy_similarity.sh)…"
  "$ROOT/deploy/deploy_similarity.sh" || true
fi

SIMILARITY_URL="${SIMILARITY_URL:-$(gcloud run services describe suffai-similarity \
  --region="$REGION" --project="$PROJECT" --format='value(status.url)' 2>/dev/null || true)}"

docker build -f "$ROOT/Dockerfile.command_center" -t "$CC_IMG" "$ROOT"
docker push "$CC_IMG"

ENV_VARS="USE_BQ=true,BQ_DATASET=suffai_ops,GOOGLE_CLOUD_PROJECT=$PROJECT,GCS_IMAGE_BUCKET=${GCS_IMAGE_BUCKET:-${PROJECT}-suffai-garment-images},EXTRACTION_URL=$WORKER_URL,CORS_ORIGINS=*,ENABLE_VERTEX=${ENABLE_VERTEX:-true},VERTEX_LOCATION=${VERTEX_LOCATION:-asia-south1}"
if [[ -n "${SIMILARITY_URL:-}" ]]; then
  ENV_VARS="${ENV_VARS},SIMILARITY_URL=${SIMILARITY_URL}"
fi

gcloud run deploy suffai-command-center \
  --image="$CC_IMG" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=1Gi \
  --set-env-vars="$ENV_VARS" \
  --project="$PROJECT"

CC_URL="$(gcloud run services describe suffai-command-center --region="$REGION" --project="$PROJECT" --format='value(status.url)')"

echo "Command Center: $CC_URL/login.html"
echo "Extraction:     $WORKER_URL"
echo "Similarity:     ${SIMILARITY_URL:-'(not deployed / set SIMILARITY_URL)'}"
echo "Done. (suffai-api retired)"
