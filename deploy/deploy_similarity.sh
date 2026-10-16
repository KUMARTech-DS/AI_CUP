#!/usr/bin/env bash
# Build & deploy suffai-similarity + Pub/Sub push (aligned with suffai-extraction).
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GCP_REGION:-asia-south1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMG="$REGION-docker.pkg.dev/$PROJECT/suffai/suffai-similarity:latest"

GCS_IMAGE_BUCKET="${GCS_IMAGE_BUCKET:-${PROJECT}-suffai-garment-images}"
ENABLE_VERTEX="${ENABLE_VERTEX:-true}"
SIMILARITY_THRESHOLD="${SIMILARITY_THRESHOLD:-0.92}"
VERTEX_LOCATION="${VERTEX_LOCATION:-$REGION}"
VERTEX_EMBED_MODEL="${VERTEX_EMBED_MODEL:-multimodalembedding@001}"
USE_BQ="${USE_BQ:-true}"
BQ_DATASET="${BQ_DATASET:-suffai_ops}"
TOPIC="${PUBSUB_IMAGE_TOPIC:-garment-image-uploaded}"
SUB="${PUBSUB_SIMILARITY_SUBSCRIPTION:-garment-image-uploaded-similarity-push}"
PUSH_SA="${PUBSUB_PUSH_SA:-pubsub-push@${PROJECT}.iam.gserviceaccount.com}"

gcloud artifacts repositories create suffai \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT" 2>/dev/null || true

docker build -f "$ROOT/Dockerfile.similarity" -t "$IMG" "$ROOT"
docker push "$IMG"

ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT},GCS_IMAGE_BUCKET=${GCS_IMAGE_BUCKET},ENABLE_VERTEX=${ENABLE_VERTEX},SIMILARITY_THRESHOLD=${SIMILARITY_THRESHOLD},VERTEX_LOCATION=${VERTEX_LOCATION},VERTEX_EMBED_MODEL=${VERTEX_EMBED_MODEL},USE_BQ=${USE_BQ},BQ_DATASET=${BQ_DATASET},CORS_ORIGINS=*"
if [[ -n "${DATABASE_URL:-}" ]]; then
  ENV_VARS="${ENV_VARS},DATABASE_URL=${DATABASE_URL}"
fi

# Match extraction: require auth (Pub/Sub OIDC invoker)
DEPLOY_ARGS=(
  --image="$IMG"
  --region="$REGION"
  --platform=managed
  --no-allow-unauthenticated
  --memory=1Gi
  --cpu=1
  --set-env-vars="$ENV_VARS"
  --project="$PROJECT"
)
if [[ -n "${CLOUD_SQL_INSTANCE:-}" ]]; then
  DEPLOY_ARGS+=(--add-cloudsql-instances="$CLOUD_SQL_INSTANCE")
fi

gcloud run deploy suffai-similarity "${DEPLOY_ARGS[@]}"

URL="$(gcloud run services describe suffai-similarity --region="$REGION" --project="$PROJECT" --format='value(status.url)')"

# Ensure push SA exists and can invoke Cloud Run (same pattern as extraction push)
gcloud iam service-accounts create pubsub-push \
  --display-name="Pub/Sub push to Cloud Run" \
  --project="$PROJECT" 2>/dev/null || true

gcloud run services add-iam-policy-binding suffai-similarity \
  --region="$REGION" \
  --project="$PROJECT" \
  --member="serviceAccount:${PUSH_SA}" \
  --role="roles/run.invoker" 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:service-$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" 2>/dev/null || true

gcloud pubsub topics create "$TOPIC" --project="$PROJECT" 2>/dev/null || true

# Second subscription on the same topic as extraction
if gcloud pubsub subscriptions describe "$SUB" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "$SUB" \
    --push-endpoint="${URL}/" \
    --push-auth-service-account="$PUSH_SA" \
    --push-auth-token-audience="$URL" \
    --project="$PROJECT"
else
  gcloud pubsub subscriptions create "$SUB" \
    --topic="$TOPIC" \
    --push-endpoint="${URL}/" \
    --push-auth-service-account="$PUSH_SA" \
    --push-auth-token-audience="$URL" \
    --project="$PROJECT"
fi

echo ""
echo "Similarity:     $URL"
echo "Health:         $URL/health  (needs identity token)"
echo "Process:        POST $URL/v1/similarity/process"
echo "Admin summary:  $URL/v1/admin/unique-garments/summary"
echo "Pub/Sub:        $TOPIC → $SUB → $URL/ (OIDC via $PUSH_SA)"
echo ""
echo "Apply: db/migrations/003_garment_similarity.sql (+ 004 if upgrading)"
echo "BQ DDL: similarity/scripts/create_bq_similarity_tables.sql (dataset $BQ_DATASET)"
echo "Set DATABASE_URL / Cloud SQL attach if not passed above."
echo "IAM: storage.objectViewer, aiplatform.user, bigquery.dataEditor, cloudsql.client"
