#!/usr/bin/env bash
# Load login_cred_db.csv into BigQuery.
# Usage:
#   ./load_login_creds_bq.sh YOUR_PROJECT_ID [path/to/login_cred_db.csv]
set -euo pipefail

PROJECT="${1:-}"
CSV="${2:-$(cd "$(dirname "$0")/../../command_center_data/auth" && pwd)/login_cred_db.csv}"
REGION="${GCP_REGION:-asia-south1}"
DATASET="suffai_ops"
BUCKET="${PROJECT}-cmd-center-raw"

if [[ -z "$PROJECT" ]]; then
  echo "Usage: ./load_login_creds_bq.sh YOUR_PROJECT_ID [login_cred_db.csv]"
  exit 1
fi
if [[ ! -f "$CSV" ]]; then
  echo "ERROR: CSV not found: $CSV"
  echo "  Run: python3 command_center/scripts/generate_login_creds.py"
  exit 1
fi

echo "Project=$PROJECT  CSV=$CSV  Dataset=$DATASET"

gcloud config set project "$PROJECT"

if ! gcloud storage buckets describe "gs://${BUCKET}" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" --project="$PROJECT" --location="$REGION"
fi

gcloud storage cp "$CSV" "gs://${BUCKET}/tables/login_cred_db.csv"

bq --location="$REGION" mk --dataset --force "${PROJECT}:${DATASET}" || true

# Ensure table exists (schema from DDL)
bq query --use_legacy_sql=false --project_id="$PROJECT" <<SQL || true
CREATE TABLE IF NOT EXISTS \`${PROJECT}.${DATASET}.login_cred_db\` (
  username STRING NOT NULL,
  password_hash STRING NOT NULL,
  role STRING NOT NULL,
  brand_name STRING,
  customer_id STRING,
  display_name STRING,
  active BOOL,
  created_at TIMESTAMP
);
SQL

bq load --replace --source_format=CSV --skip_leading_rows=1 \
  --project_id="$PROJECT" \
  "${DATASET}.login_cred_db" \
  "gs://${BUCKET}/tables/login_cred_db.csv" \
  username:STRING,password_hash:STRING,role:STRING,brand_name:STRING,customer_id:STRING,display_name:STRING,active:BOOLEAN,created_at:TIMESTAMP

echo "Done. ${PROJECT}.${DATASET}.login_cred_db loaded from $CSV"
