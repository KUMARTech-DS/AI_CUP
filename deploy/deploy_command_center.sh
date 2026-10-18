#!/usr/bin/env bash
# Build & deploy suffai-command-center only (does not change extraction / similarity app code).
# Uses --update-env-vars so existing keys (e.g. DATABASE_URL from Secret Manager) are preserved.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GCP_REGION:-asia-south1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMG="$REGION-docker.pkg.dev/$PROJECT/suffai/suffai-command-center:latest"

# Optional overrides
USE_BQ="${USE_BQ:-true}"
BQ_DATASET="${BQ_DATASET:-suffai_ops}"
EXTRACTION_URL="${EXTRACTION_URL:-}"
SIMILARITY_URL="${SIMILARITY_URL:-}"
GCS_IMAGE_BUCKET="${GCS_IMAGE_BUCKET:-${PROJECT}-suffai-garment-images}"
ENABLE_VERTEX="${ENABLE_VERTEX:-true}"
VERTEX_LOCATION="${VERTEX_LOCATION:-asia-south1}"
VERTEX_CHAT_MODEL="${VERTEX_CHAT_MODEL:-gemini-2.5-flash}"

gcloud artifacts repositories create suffai \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT" 2>/dev/null || true

docker build -f "$ROOT/Dockerfile.command_center" -t "$IMG" "$ROOT"
docker push "$IMG"

ENV_VARS="USE_BQ=${USE_BQ},BQ_DATASET=${BQ_DATASET},GOOGLE_CLOUD_PROJECT=${PROJECT},GCS_IMAGE_BUCKET=${GCS_IMAGE_BUCKET},CORS_ORIGINS=*,ENABLE_VERTEX=${ENABLE_VERTEX},VERTEX_LOCATION=${VERTEX_LOCATION},VERTEX_CHAT_MODEL=${VERTEX_CHAT_MODEL}"
if [[ -n "$EXTRACTION_URL" ]]; then
  ENV_VARS="${ENV_VARS},EXTRACTION_URL=${EXTRACTION_URL}"
fi
if [[ -n "$SIMILARITY_URL" ]]; then
  ENV_VARS="${ENV_VARS},SIMILARITY_URL=${SIMILARITY_URL}"
fi
if [[ -n "${DATABASE_URL:-}" ]]; then
  ENV_VARS="${ENV_VARS},DATABASE_URL=${DATABASE_URL}"
fi

DEPLOY_ARGS=(
  --image="$IMG"
  --region="$REGION"
  --platform=managed
  --allow-unauthenticated
  --memory=1Gi
  --cpu=1
  --update-env-vars="$ENV_VARS"
  --project="$PROJECT"
)
if [[ -n "${CLOUD_SQL_INSTANCE:-}" ]]; then
  DEPLOY_ARGS+=(--add-cloudsql-instances="$CLOUD_SQL_INSTANCE")
fi

gcloud run deploy suffai-command-center "${DEPLOY_ARGS[@]}"

URL="$(gcloud run services describe suffai-command-center --region="$REGION" --project="$PROJECT" --format='value(status.url)')"
echo ""
echo "Command Center: $URL/login   (tabs: /geo /ads-lab /campaigns /style-assistant /admin)"
echo "Health:         $URL/health"
echo "Metrics:        $URL/v1/metrics"
echo "Extract:        POST $URL/v1/ads-lab/extract"
echo "Campaign:       POST $URL/v1/ads-lab/campaign  (needs SIMILARITY_URL + DATABASE_URL)"
echo "Chat:           POST $URL/v1/chat  (ENABLE_VERTEX=${ENABLE_VERTEX}; uses web/metrics.json)"
echo "Admin:          GET  $URL/v1/admin/unique-summary  (proxies similarity)"
echo "Style:          GET  $URL/v1/style-assistant/feed|users"
echo ""
echo "Redeploy guide: docs/GCP_REDEPLOY_E2E.md"
