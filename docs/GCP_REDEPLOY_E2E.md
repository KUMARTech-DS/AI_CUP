# End-to-end GCP redeploy guide (services already live)

Use this when **Cloud Run / GCS / BigQuery / Cloud SQL / Pub/Sub already exist** and you need to ship the current **`AI_CUP_latest`** changes (campaign Ads Lab, Style Assistant Feature 2, corner Bot chat, Admin proxy, brand SKU tables).

Canonical folder:

```text
/Users/rahul.kumar/Downloads/AI_CUP/AI_CUP_latest
```

Do **not** deploy from `AI_CUP-main/temp/` (pre–Feature 1 archive).

---

## What gets redeployed

| Cloud Run service | Source in repo | Redeploy? | Why |
|-------------------|----------------|-----------|-----|
| `suffai-command-center` | `Dockerfile.command_center` → `command_center/` + `web/` | **Yes (required)** | Campaign API, Style Assistant (`/v1/style-assistant/*`), chat, Admin proxy, UI tabs |
| `suffai-similarity` | `Dockerfile.similarity` → `similarity/` | **Yes if compare-pins / admin unique-garments changed** | Campaign ranking + Admin proxy target |
| `suffai-extraction` | `Dockerfile.worker` → `worker/` | **Only if worker code changed** | Ads Lab still calls existing `/v1/extract`; usually skip |
| Jobs / Scheduler / CDC | `deploy/scheduler.yaml` | **No** for this feature set | Leave as-is |

Retired: `suffai-api` — do not recreate.

---

## Prerequisites (local)

```bash
# From AI_CUP_latest
cd /Users/rahul.kumar/Downloads/AI_CUP/AI_CUP_latest

gcloud auth login
gcloud auth configure-docker asia-south1-docker.pkg.dev

export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID   # replace
export GCP_REGION=asia-south1
gcloud config set project "$GOOGLE_CLOUD_PROJECT"
gcloud config set run/region "$GCP_REGION"

# Docker must be running for local build+push, OR use Cloud Build (below)
```

Confirm live services:

```bash
gcloud run services list --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='table(metadata.name,status.url)'
```

You should see at least `suffai-command-center`, `suffai-extraction`, `suffai-similarity`.

---

## Step 0 — Capture current URLs & env (do this first)

```bash
export EXTRACTION_URL=$(gcloud run services describe suffai-extraction \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)')

export SIMILARITY_URL=$(gcloud run services describe suffai-similarity \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)')

export CC_URL=$(gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)')

echo "EXTRACTION_URL=$EXTRACTION_URL"
echo "SIMILARITY_URL=$SIMILARITY_URL"
echo "CC_URL=$CC_URL"

# Snapshot env (so you can restore DATABASE_URL / secrets after deploy)
gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='yaml(spec.template.spec.containers[0].env)' > /tmp/cc-env-before.yaml

gcloud run services describe suffai-similarity \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='yaml(spec.template.spec.containers[0].env)' > /tmp/sim-env-before.yaml
```

Note Cloud SQL instance connection name if used (e.g. `PROJECT:asia-south1:suffai-ops`):

```bash
gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(spec.template.metadata.annotations[run.googleapis.com/cloudsql-instances])'
```

Export for deploy scripts when needed:

```bash
# Example — use your real values from the snapshot / Secret Manager
# export DATABASE_URL='postgresql://USER:PASS@/DB?host=/cloudsql/PROJECT:REGION:INSTANCE'
# export CLOUD_SQL_INSTANCE='PROJECT:REGION:INSTANCE'
export GCS_IMAGE_BUCKET="${GCS_IMAGE_BUCKET:-${GOOGLE_CLOUD_PROJECT}-suffai-garment-images}"
export ENABLE_VERTEX=true
export VERTEX_LOCATION=asia-south1
export VERTEX_CHAT_MODEL=gemini-2.5-flash
```

---

## Step 0b — BigQuery login credentials (role ACL)

Required for `POST /v1/auth/login` (brand / customer / admin tabs). No new secrets — uses existing project ADC / Cloud Run SA for BQ.

```bash
python3 command_center/scripts/generate_login_creds.py
chmod +x command_center/scripts/load_login_creds_bq.sh
./command_center/scripts/load_login_creds_bq.sh "$GOOGLE_CLOUD_PROJECT"
# Keep command_center_data/auth/login_creds_plaintext.csv offline (gitignored).
```

Verify:

```bash
bq query --use_legacy_sql=false \
  "SELECT role, COUNT(*) AS n FROM \`${GOOGLE_CLOUD_PROJECT}.suffai_ops.login_cred_db\` GROUP BY role"
```

---

## Step 1 — Cloud SQL migration (brand campaign tables)

Required for Ads Lab **campaign** persistence (`brand_campaigns`, `brand_skus`, `brand_sku_pin_scores`).

**Option A — script (if `psql` + `DATABASE_URL` work):**

```bash
chmod +x deploy/01_prepare_similarity_db.sh
# Prefer applying only 005 if 003/004 already applied:
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/005_brand_sku_campaigns.sql
# Or full prepare (003+004+005 + BQ DDL):
./deploy/01_prepare_similarity_db.sh
```

**Option B — Cloud SQL Studio / Console:** paste and run `db/migrations/005_brand_sku_campaigns.sql`.

Verify:

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('brand_campaigns','brand_skus','brand_sku_pin_scores');
```

---

## Step 2 — Redeploy `suffai-similarity` (if needed)

Skip if you only changed Command Center UI/BFF and similarity image is already current with `compare-pins` + admin APIs.

### 2a. Build & deploy

```bash
chmod +x deploy/deploy_similarity.sh deploy/02_deploy_similarity.sh

# Pass DATABASE_URL + CLOUD_SQL_INSTANCE so they are not lost
./deploy/02_deploy_similarity.sh
# same as: ./deploy/deploy_similarity.sh
```

### 2b. Or Cloud Build then deploy image

```bash
gcloud builds submit --config=deploy/cloudbuild-similarity.yaml .

IMG="${GCP_REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/suffai/suffai-similarity:latest"
gcloud run deploy suffai-similarity \
  --image="$IMG" \
  --region="$GCP_REGION" \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --no-allow-unauthenticated
```

After image-only deploy, **re-check** that `DATABASE_URL`, Cloud SQL attachment, and Pub/Sub push endpoint still point at the new revision (`deploy/deploy_similarity.sh` updates the push sub).

### 2c. IAM (usually already set)

- Pub/Sub push SA `pubsub-push@…` → `roles/run.invoker` on `suffai-similarity`
- Similarity SA: Cloud SQL Client, GCS objectViewer, Vertex AI User, BigQuery as configured

---

## Step 3 — Redeploy `suffai-command-center` (always for this release)

This ships Geo + Ads Lab campaign UI + Bot + Admin tab.

```bash
chmod +x deploy/deploy_command_center.sh deploy/04_integrate_command_center.sh

# Resolves EXTRACTION_URL + SIMILARITY_URL from live services, then builds/pushes/deploys CC
./deploy/04_integrate_command_center.sh
```

Or explicitly:

```bash
export EXTRACTION_URL SIMILARITY_URL   # from Step 0
./deploy/deploy_command_center.sh
```

### Cloud Build alternative

```bash
gcloud builds submit --config=deploy/cloudbuild-command-center.yaml .

IMG="${GCP_REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/suffai/suffai-command-center:latest"
gcloud run deploy suffai-command-center \
  --image="$IMG" \
  --region="$GCP_REGION" \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --allow-unauthenticated \
  --memory=1Gi \
  --update-env-vars="USE_BQ=true,BQ_DATASET=suffai_ops,GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},GCS_IMAGE_BUCKET=${GCS_IMAGE_BUCKET},EXTRACTION_URL=${EXTRACTION_URL},SIMILARITY_URL=${SIMILARITY_URL},ENABLE_VERTEX=true,VERTEX_LOCATION=${VERTEX_LOCATION},VERTEX_CHAT_MODEL=${VERTEX_CHAT_MODEL},CORS_ORIGINS=*"
```

Prefer `--update-env-vars` over `--set-env-vars` so existing keys (e.g. `DATABASE_URL` from Secret Manager) are not wiped.

### Attach Cloud SQL + DATABASE_URL on CC (campaign DB)

If campaign tables live on the same Postgres as extraction/similarity:

```bash
# Attach instance (idempotent)
gcloud run services update suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --add-cloudsql-instances="$CLOUD_SQL_INSTANCE"

# Set DATABASE_URL (or map from Secret Manager in Console)
gcloud run services update suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --update-env-vars="DATABASE_URL=${DATABASE_URL}"
```

### IAM: CC → extraction + similarity

Command Center calls private Cloud Run services with an ID token. Grant the **command-center runtime service account** `roles/run.invoker` on both:

```bash
CC_SA=$(gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(spec.template.spec.serviceAccountName)')
# If empty, default Compute SA:
# CC_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud run services add-iam-policy-binding suffai-extraction \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --member="serviceAccount:${CC_SA}" \
  --role="roles/run.invoker"

gcloud run services add-iam-policy-binding suffai-similarity \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --member="serviceAccount:${CC_SA}" \
  --role="roles/run.invoker"
```

Also ensure CC SA has:

| Role / access | Needed for |
|---------------|------------|
| BigQuery Job User + Data Viewer on `suffai_ops` | `GET /v1/metrics` |
| Storage Object Creator/Viewer on garment bucket | Ads Lab upload `demo/` + `brands/` |
| Vertex AI User | `POST /v1/chat` |
| Cloud SQL Client | brand campaign tables |

---

## Step 4 — Redeploy extraction (optional)

Only if `worker/` changed:

```bash
./deploy/deploy_cloud_run.sh
# Or build Dockerfile.worker and gcloud run deploy suffai-extraction …
```

Otherwise leave the live `suffai-extraction` alone — campaign still uses its `/v1/extract`.

---

## Step 5 — Refresh `web/metrics.json` for chat (recommended)

Chat slims **`web/metrics.json` baked into the CC image**. After BQ data changes, regenerate and redeploy CC, or bake a fresh file before build:

```bash
# Needs ADC + BQ access
export USE_BQ=true GOOGLE_CLOUD_PROJECT BQ_DATASET=suffai_ops
python3 command_center/scripts/aggregate_metrics.py --bq --project "$GOOGLE_CLOUD_PROJECT"
# Writes web/metrics.json — then rebuild/redeploy command-center
```

Geo UI itself uses live `GET /v1/metrics` (BQ), not this file.

---

## Step 6 — Verify end-to-end

```bash
chmod +x deploy/05_verify_similarity_e2e.sh
./deploy/05_verify_similarity_e2e.sh
```

Manual checks:

```bash
CC_URL=$(gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)')

# Health flags
curl -sS "$CC_URL/health" | python3 -m json.tool
# Expect: use_bq, extraction_url, similarity_url, enable_vertex, ads_lab_campaign, admin_proxy, style_assistant, auth true

# Auth (admin seed)
curl -sS -X POST "$CC_URL/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin1","password":"123@admin"}' | python3 -m json.tool
# Expect: token, role=admin, allowed_tabs including geo/ads/style/admin

# Metrics (BQ)
curl -sS "$CC_URL/v1/metrics" | head -c 400; echo

# Admin proxy → similarity
curl -sS "$CC_URL/v1/admin/unique-summary" | head -c 400; echo

# Style Assistant (Feature 2)
# Customer login only; brand / admin tokens get 403
# curl -sS -H "Authorization: Bearer CUSTOMER_TOKEN" "$CC_URL/v1/style-assistant/feed" | head -c 400; echo

# Chat
curl -sS -X POST "$CC_URL/v1/chat" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Summarize top brands","context":{"active_tab":"geo"},"filter_context":{"pin_code":"110001"}}'

# UI
open "${CC_URL}/login.html"   # admin1 / 123@admin or brand_*/cust_* from plaintext handoff
```

### UI smoke checklist

| Tab | Action | Expect |
|-----|--------|--------|
| Login | Sign in as brand / customer / admin | Tabs match role; deep-link to forbidden page redirects |
| Geo (brand) | Brand filter | Only own brand + Competitors |
| Ads Lab (brand) | Brand field locked; upload SKUs | Campaign posts forced brand; taste brands collapsed |
| Style (customer) | Customer locked; pick brand → Generate | Wardrobe for session `customer_id` only |
| Bot | Ask about filters (Geo/Ads only) | Vertex answer; hidden for customer / Style / Admin |
| Admin | PIN/brand filters | Unique garments via CC proxy |

### Logs

```bash
gcloud run services logs read suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" --limit=50

gcloud run services logs read suffai-similarity \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" --limit=50
```

Look for `[command_center]` and campaign / chat / similarity proxy errors.

---

## Recommended order (short)

```text
0. Load BQ login_cred_db (generate_login_creds.py + load_login_creds_bq.sh)
1. Apply db/migrations/005_brand_sku_campaigns.sql
2. Redeploy suffai-similarity   (if similarity code/image needs update)
3. Redeploy suffai-command-center with EXTRACTION_URL + SIMILARITY_URL
4. Ensure CC has DATABASE_URL + Cloud SQL + run.invoker on extraction & similarity
5. ./deploy/05_verify_similarity_e2e.sh + UI smoke (role logins)
```

One-liner path when similarity is already good:

```bash
cd /Users/rahul.kumar/Downloads/AI_CUP/AI_CUP_latest
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID GCP_REGION=asia-south1
# export DATABASE_URL=… CLOUD_SQL_INSTANCE=…   # if not already on the service
./deploy/04_integrate_command_center.sh
./deploy/05_verify_similarity_e2e.sh
```

---

## Common failures

| Symptom | Fix |
|---------|-----|
| `/health` → `similarity_url: false` | Re-run `./deploy/04_integrate_command_center.sh` with live `SIMILARITY_URL` |
| Admin proxy 403/401 | CC SA missing `roles/run.invoker` on `suffai-similarity` |
| Admin / campaign 503 `DATABASE_URL` | Set `DATABASE_URL` + Cloud SQL on CC (and similarity for unique garments) |
| Campaign extract fails | `EXTRACTION_URL` + CC SA invoker on extraction; GCS bucket writable |
| Chat 503 | `ENABLE_VERTEX=true` + Vertex AI User on CC SA + `web/metrics.json` in image |
| Metrics 503 | `USE_BQ=true`, BQ tables/IAM, `pin_population.json` in image |
| Env vars disappeared after deploy | You used `--set-env-vars` wipe — restore from `/tmp/cc-env-before.yaml` or Secrets; prefer `--update-env-vars` |

---

## Related docs

- APIs & layout: [GCP_COMMAND_CENTER.md](./GCP_COMMAND_CENTER.md)
- Greenfield (from empty project): [GCP_DEPLOY_STEP_BY_STEP.md](./GCP_DEPLOY_STEP_BY_STEP.md)
- Similarity E2E scripts: `deploy/01_…` through `deploy/05_…`
