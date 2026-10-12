#!/usr/bin/env bash
# Deploy API + Web + Worker to Cloud Run. Requires: gcloud auth, project set.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GCP_REGION:-asia-south1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Project=$PROJECT Region=$REGION"

gcloud artifacts repositories create suffai \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT" 2>/dev/null || true

API_IMG="$REGION-docker.pkg.dev/$PROJECT/suffai/suffai-api:latest"
WEB_IMG="$REGION-docker.pkg.dev/$PROJECT/suffai/suffai-web:latest"
WORKER_IMG="$REGION-docker.pkg.dev/$PROJECT/suffai/suffai-extraction:latest"

docker build -f "$ROOT/Dockerfile.api" -t "$API_IMG" "$ROOT"
docker push "$API_IMG"

gcloud run deploy suffai-api \
  --image="$API_IMG" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --set-env-vars="ENVIRONMENT=staging,DEMO_AUTH_ENABLED=true,GCP_REGION=$REGION,GOOGLE_CLOUD_PROJECT=$PROJECT" \
  --project="$PROJECT"

API_URL="$(gcloud run services describe suffai-api --region="$REGION" --project="$PROJECT" --format='value(status.url)')"
echo "API_URL=$API_URL"

docker build -f "$ROOT/Dockerfile.web" \
  --build-arg "NEXT_PUBLIC_API_URL=$API_URL" \
  -t "$WEB_IMG" "$ROOT"
docker push "$WEB_IMG"

gcloud run deploy suffai-web \
  --image="$WEB_IMG" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --set-env-vars="PORT=8080" \
  --project="$PROJECT"

# Point API CORS at web
WEB_URL="$(gcloud run services describe suffai-web --region="$REGION" --project="$PROJECT" --format='value(status.url)')"
gcloud run services update suffai-api \
  --region="$REGION" \
  --project="$PROJECT" \
  --update-env-vars="WEB_ORIGIN=$WEB_URL,API_PUBLIC_URL=$API_URL"

docker build -f "$ROOT/Dockerfile.worker" -t "$WORKER_IMG" "$ROOT"
docker push "$WORKER_IMG"

gcloud run deploy suffai-extraction \
  --image="$WORKER_IMG" \
  --region="$REGION" \
  --platform=managed \
  --no-allow-unauthenticated \
  --set-env-vars="ENABLE_VERTEX=false" \
  --project="$PROJECT"

echo "Web: $WEB_URL"
echo "API: $API_URL"
echo "Done."
