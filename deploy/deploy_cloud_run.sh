#!/usr/bin/env bash
# GCP-native deploy: Cloud Build images → Cloud Run (api, web, worker) + Cloud Run Jobs,
# Cloud SQL for operational records, BigQuery for dashboards, GCS for source JSON + images,
# Pub/Sub for GCS upload events, Secret Manager for credentials. Safe to re-run.
#
# Required:
#   GOOGLE_CLOUD_PROJECT       e.g. suffai-dev
#   CLOUD_SQL_INSTANCE_NAME    existing Cloud SQL Postgres instance name (e.g. suffai-pg)
#   Secret `suffai-database-url` holding e.g.
#     postgresql://USER:PASS@/suffai?host=/cloudsql/PROJECT:REGION:INSTANCE
# Optional:
#   GCP_REGION (asia-south1), UPLOAD_SOURCE_DATA=true to (re)upload data_offline/*.json to GCS,
#   VERTEX_MODEL to override the worker model.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GCP_REGION:-asia-south1}"
SQL_INSTANCE_NAME="${CLOUD_SQL_INSTANCE_NAME:?Set CLOUD_SQL_INSTANCE_NAME}"
SQL_CONNECTION="$PROJECT:$REGION:$SQL_INSTANCE_NAME"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

RAW_BUCKET="${GCS_RAW_BUCKET:-$PROJECT-suffai-raw}"
IMAGE_BUCKET="${GCS_IMAGE_BUCKET:-$PROJECT-suffai-garment-images}"
TOPIC="garment-image-uploaded"
REPO="$REGION-docker.pkg.dev/$PROJECT/suffai"
API_SA="suffai-api@$PROJECT.iam.gserviceaccount.com"
WORKER_SA="suffai-extraction@$PROJECT.iam.gserviceaccount.com"
JOB_SA="suffai-ingestion@$PROJECT.iam.gserviceaccount.com"
PUSH_SA="suffai-pubsub-push@$PROJECT.iam.gserviceaccount.com"
SCHED_SA="suffai-scheduler@$PROJECT.iam.gserviceaccount.com"

g() { gcloud "$@" --project="$PROJECT"; }
quiet() { "$@" >/dev/null 2>&1 || true; }

echo "== Project=$PROJECT Region=$REGION SQL=$SQL_CONNECTION"

echo "== APIs"
g services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  sqladmin.googleapis.com bigquery.googleapis.com storage.googleapis.com pubsub.googleapis.com \
  secretmanager.googleapis.com aiplatform.googleapis.com cloudscheduler.googleapis.com \
  iamcredentials.googleapis.com

echo "== Artifact Registry, buckets, BigQuery datasets, Pub/Sub topic"
quiet g artifacts repositories create suffai --repository-format=docker --location="$REGION"
quiet g storage buckets create "gs://$RAW_BUCKET" --location="$REGION" --uniform-bucket-level-access
quiet g storage buckets create "gs://$IMAGE_BUCKET" --location="$REGION" --uniform-bucket-level-access
for ds in suffai_raw suffai_staging suffai_marts; do
  quiet bq --project_id="$PROJECT" --location="$REGION" mk --dataset "$PROJECT:$ds"
done
quiet g pubsub topics create "$TOPIC"
quiet g pubsub topics create "$TOPIC-dlq"

if [[ "${UPLOAD_SOURCE_DATA:-false}" == "true" ]]; then
  echo "== Uploading source JSON snapshots to gs://$RAW_BUCKET/data_offline/"
  g storage cp "$ROOT"/data_offline/*.json "gs://$RAW_BUCKET/data_offline/"
fi

echo "== Service accounts + IAM"
for sa in suffai-api suffai-extraction suffai-ingestion suffai-pubsub-push suffai-scheduler; do
  quiet g iam service-accounts create "$sa" --display-name="$sa"
done
bind() { quiet g projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$1" --role="$2" --condition=None; }
# aiplatform.user: SKU ranking (Ads Lab) extracts attributes from in-memory images with Vertex Gemini.
for role in roles/cloudsql.client roles/bigquery.jobUser roles/bigquery.dataEditor roles/pubsub.publisher roles/secretmanager.secretAccessor roles/aiplatform.user; do
  bind "$API_SA" "$role"
done
for role in roles/cloudsql.client roles/bigquery.jobUser roles/bigquery.dataEditor roles/aiplatform.user roles/secretmanager.secretAccessor; do
  bind "$WORKER_SA" "$role"
done
for role in roles/bigquery.jobUser roles/bigquery.dataEditor; do
  bind "$JOB_SA" "$role"
done
quiet g storage buckets add-iam-policy-binding "gs://$IMAGE_BUCKET" --member="serviceAccount:$API_SA" --role=roles/storage.objectAdmin
quiet g storage buckets add-iam-policy-binding "gs://$IMAGE_BUCKET" --member="serviceAccount:$WORKER_SA" --role=roles/storage.objectViewer
quiet g storage buckets add-iam-policy-binding "gs://$RAW_BUCKET" --member="serviceAccount:$API_SA" --role=roles/storage.objectViewer
quiet g storage buckets add-iam-policy-binding "gs://$RAW_BUCKET" --member="serviceAccount:$JOB_SA" --role=roles/storage.objectAdmin
# Signed upload URLs from Cloud Run go through IAM signBlob as the API's own SA.
quiet g iam service-accounts add-iam-policy-binding "$API_SA" --member="serviceAccount:$API_SA" --role=roles/iam.serviceAccountTokenCreator
# GCS publishes upload notifications to Pub/Sub as its service agent.
GCS_AGENT="$(g storage service-agent)"
quiet g pubsub topics add-iam-policy-binding "$TOPIC" --member="serviceAccount:$GCS_AGENT" --role=roles/pubsub.publisher

echo "== Secrets"
if ! g secrets describe suffai-database-url >/dev/null 2>&1; then
  echo "Missing secret suffai-database-url. Create it with:" >&2
  echo "  printf 'postgresql://USER:PASS@/suffai?host=/cloudsql/$SQL_CONNECTION' | gcloud secrets create suffai-database-url --data-file=- --project=$PROJECT" >&2
  exit 1
fi
if ! g secrets describe suffai-jwt-secret >/dev/null 2>&1; then
  openssl rand -hex 32 | tr -d '\n' | g secrets create suffai-jwt-secret --data-file=-
fi

echo "== Build images with Cloud Build"
g builds submit "$ROOT" --config="$ROOT/deploy/cloudbuild-api.yaml" --substitutions=_REGION="$REGION"
g builds submit "$ROOT" --config="$ROOT/deploy/cloudbuild-worker.yaml" --substitutions=_REGION="$REGION"
g builds submit "$ROOT" --config="$ROOT/deploy/cloudbuild-job.yaml" --substitutions=_REGION="$REGION"

COMMON_ENV="GOOGLE_CLOUD_PROJECT=$PROJECT,GCP_REGION=$REGION,BQ_PROJECT=$PROJECT,BQ_LOCATION=$REGION"
DATA_ENV="GCS_RAW_BUCKET=$RAW_BUCKET,GCS_DATA_PREFIX=data_offline/,GCS_IMAGE_BUCKET=$IMAGE_BUCKET"

echo "== Deploy suffai-extraction (worker)"
WORKER_ENV="$COMMON_ENV,GCS_IMAGE_BUCKET=$IMAGE_BUCKET,ENABLE_VERTEX=true,ENABLE_BIGQUERY=true,VERTEX_LOCATION=$REGION"
[[ -n "${VERTEX_MODEL:-}" ]] && WORKER_ENV="$WORKER_ENV,VERTEX_MODEL=$VERTEX_MODEL"
g run deploy suffai-extraction \
  --image="$REPO/suffai-extraction:latest" --region="$REGION" --platform=managed \
  --no-allow-unauthenticated --service-account="$WORKER_SA" \
  --add-cloudsql-instances="$SQL_CONNECTION" \
  --set-secrets="DATABASE_URL=suffai-database-url:latest" \
  --update-env-vars="$WORKER_ENV"
WORKER_URL="$(g run services describe suffai-extraction --region="$REGION" --format='value(status.url)')"

echo "== Pub/Sub push subscription → worker, GCS upload notification → topic"
quiet g run services add-iam-policy-binding suffai-extraction --region="$REGION" \
  --member="serviceAccount:$PUSH_SA" --role=roles/run.invoker
if g pubsub subscriptions describe "$TOPIC-push" >/dev/null 2>&1; then
  g pubsub subscriptions update "$TOPIC-push" --push-endpoint="$WORKER_URL/" \
    --push-auth-service-account="$PUSH_SA"
else
  g pubsub subscriptions create "$TOPIC-push" --topic="$TOPIC" \
    --push-endpoint="$WORKER_URL/" --push-auth-service-account="$PUSH_SA" \
    --ack-deadline=300 --dead-letter-topic="$TOPIC-dlq" --max-delivery-attempts=5
fi
if ! g storage buckets notifications list "gs://$IMAGE_BUCKET" --format='value(topic)' 2>/dev/null | grep -q "$TOPIC"; then
  g storage buckets notifications create "gs://$IMAGE_BUCKET" --topic="$TOPIC" \
    --event-types=OBJECT_FINALIZE --payload-format=json
fi

echo "== Deploy suffai-api"
# SKU ranking (POST /api/v1/sku/rank): up to 20 images per request stay in memory and go to
# Vertex inline, so the API needs Vertex env, headroom for Pillow decoding, and a longer timeout.
API_ENV="ENVIRONMENT=staging,$COMMON_ENV,$DATA_ENV,USE_BIGQUERY_MARTS=true,CLOUD_SQL_INSTANCE=$SQL_CONNECTION,PUBSUB_IMAGE_TOPIC=$TOPIC"
API_ENV="$API_ENV,ENABLE_VERTEX=${API_ENABLE_VERTEX:-true},VERTEX_LOCATION=$REGION,SKU_RANK_CONSENT_MODE=${SKU_RANK_CONSENT_MODE:-opt_out}"
[[ -n "${VERTEX_MODEL:-}" ]] && API_ENV="$API_ENV,VERTEX_MODEL=$VERTEX_MODEL"
g run deploy suffai-api \
  --image="$REPO/suffai-api:latest" --region="$REGION" --platform=managed \
  --allow-unauthenticated --service-account="$API_SA" \
  --add-cloudsql-instances="$SQL_CONNECTION" \
  --memory=2Gi --cpu=2 --timeout=300 --concurrency=40 \
  --set-secrets="DATABASE_URL=suffai-database-url:latest,JWT_SECRET=suffai-jwt-secret:latest" \
  --update-env-vars="$API_ENV"
API_URL="$(g run services describe suffai-api --region="$REGION" --format='value(status.url)')"

echo "== Deploy suffai-web"
g builds submit "$ROOT" --config="$ROOT/deploy/cloudbuild-web.yaml" \
  --substitutions=_REGION="$REGION",_API_URL="$API_URL"
g run deploy suffai-web \
  --image="$REPO/suffai-web:latest" --region="$REGION" --platform=managed \
  --allow-unauthenticated --update-env-vars="PORT=8080"
WEB_URL="$(g run services describe suffai-web --region="$REGION" --format='value(status.url)')"
g run services update suffai-api --region="$REGION" \
  --update-env-vars="WEB_ORIGIN=$WEB_URL,API_PUBLIC_URL=$API_URL"

echo "== Cloud Run Jobs (BigQuery sync / CDC) + Scheduler"
JOB_ENV="$COMMON_ENV,$DATA_ENV"
for job in "suffai-bq-sync:jobs.bq_sync" "suffai-cdc:jobs.cdc_ingest"; do
  name="${job%%:*}"; module="${job##*:}"
  if g run jobs describe "$name" --region="$REGION" >/dev/null 2>&1; then
    g run jobs update "$name" --region="$REGION" --image="$REPO/suffai-job:latest" \
      --service-account="$JOB_SA" --update-env-vars="$JOB_ENV" --args="$module"
  else
    g run jobs create "$name" --region="$REGION" --image="$REPO/suffai-job:latest" \
      --service-account="$JOB_SA" --set-env-vars="$JOB_ENV" --args="$module" --task-timeout=1800
  fi
done
quiet g run jobs add-iam-policy-binding suffai-bq-sync --region="$REGION" --member="serviceAccount:$SCHED_SA" --role=roles/run.invoker
quiet g run jobs add-iam-policy-binding suffai-cdc --region="$REGION" --member="serviceAccount:$SCHED_SA" --role=roles/run.invoker
RUN_BASE="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs"
quiet g scheduler jobs create http suffai-bq-marts-daily --location="$REGION" \
  --schedule="15 2 * * *" --time-zone="Asia/Kolkata" --http-method=POST \
  --uri="$RUN_BASE/suffai-bq-sync:run" --oauth-service-account-email="$SCHED_SA"

echo "== Initial BigQuery load (orders, customers, stores, garment table + view)"
g run jobs execute suffai-bq-sync --region="$REGION" --wait

echo "Web:    $WEB_URL"
echo "API:    $API_URL   (check $API_URL/health → storage.gcp_native == true)"
echo "Worker: $WORKER_URL"
