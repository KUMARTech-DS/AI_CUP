#!/usr/bin/env bash
# Build & deploy suffai-command-center only (does not change suffai-api / extraction).
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GCP_REGION:-asia-south1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMG="$REGION-docker.pkg.dev/$PROJECT/suffai/suffai-command-center:latest"

# Optional overrides
USE_BQ="${USE_BQ:-true}"
BQ_DATASET="${BQ_DATASET:-suffai_ops}"
EXTRACTION_URL="${EXTRACTION_URL:-}"
GCS_IMAGE_BUCKET="${GCS_IMAGE_BUCKET:-${PROJECT}-suffai-garment-images}"

gcloud artifacts repositories create suffai \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT" 2>/dev/null || true

docker build -f "$ROOT/Dockerfile.command_center" -t "$IMG" "$ROOT"
docker push "$IMG"

ENV_VARS="USE_BQ=${USE_BQ},BQ_DATASET=${BQ_DATASET},GOOGLE_CLOUD_PROJECT=${PROJECT},GCS_IMAGE_BUCKET=${GCS_IMAGE_BUCKET},CORS_ORIGINS=*"
if [[ -n "$EXTRACTION_URL" ]]; then
  ENV_VARS="${ENV_VARS},EXTRACTION_URL=${EXTRACTION_URL}"
fi

gcloud run deploy suffai-command-center \
  --image="$IMG" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=1Gi \
  --cpu=1 \
  --set-env-vars="$ENV_VARS" \
  --project="$PROJECT"

# Optional: attach Cloud SQL if DATABASE_URL uses unix socket — set DATABASE_URL via Secret Manager in console.
URL="$(gcloud run services describe suffai-command-center --region="$REGION" --project="$PROJECT" --format='value(status.url)')"
echo ""
echo "Command Center: $URL/login.html"
echo "Health:         $URL/health"
echo "Metrics:        $URL/v1/metrics"
echo "Extract:        POST $URL/v1/ads-lab/extract"
