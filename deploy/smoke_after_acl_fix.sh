#!/usr/bin/env bash
# Redeploy Command Center after ACL/conflict fix, reload login_cred_db, smoke roles+metrics.
# Requires: gcloud, bq, Docker (for deploy_command_center.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
export GCP_REGION="${GCP_REGION:-asia-south1}"
gcloud config set project "$GOOGLE_CLOUD_PROJECT"
gcloud config set run/region "$GCP_REGION"

echo "==> Ensure login credentials CSV exists"
if [[ ! -f command_center_data/auth/login_cred_db.csv ]]; then
  python3 command_center/scripts/generate_login_creds.py
fi

echo "==> Load login_cred_db into BigQuery"
chmod +x command_center/scripts/load_login_creds_bq.sh
./command_center/scripts/load_login_creds_bq.sh "$GOOGLE_CLOUD_PROJECT"

echo "==> Redeploy suffai-command-center"
chmod +x deploy/04_integrate_command_center.sh deploy/deploy_command_center.sh
./deploy/04_integrate_command_center.sh

CC_URL="$(gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)')"
echo "CC_URL=$CC_URL"

echo "==> Smoke admin/brand/customer login + /v1/metrics"
chmod +x command_center/scripts/smoke_role_auth.sh
CC_URL="$CC_URL" ./command_center/scripts/smoke_role_auth.sh

echo ""
echo "Done. Open: $CC_URL/login.html"
echo "Admin seed: admin1 / 123@admin"
echo "Brand/customer passwords: command_center_data/auth/login_creds_plaintext.csv"
