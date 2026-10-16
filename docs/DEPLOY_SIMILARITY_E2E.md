# End-to-end: deploy `suffai-similarity` + integrate extraction & command center

Step-by-step GCP deployment. **Does not change** `suffai-extraction` application code (`worker/`).

Related: [GARMENT_SIMILARITY.md](GARMENT_SIMILARITY.md) · [GCP_COMMAND_CENTER.md](GCP_COMMAND_CENTER.md)

---

## Architecture (what you are wiring)

```text
GCS (same bucket)
    └─ Pub/Sub topic: garment-image-uploaded
           ├─ push sub (existing) ──► suffai-extraction   (attributes / Vertex Gemini)
           └─ push sub (new)      ──► suffai-similarity   (embeddings / wardrobe / PIN)

suffai-command-center
    ├─ Ads Lab  ──EXTRACTION_URL──► suffai-extraction
    └─ Admin    ──SIMILARITY_URL──► suffai-similarity
```

---

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| `gcloud` logged in | `gcloud auth login` + project set |
| Docker | Build & push images |
| APIs | Cloud Run, Artifact Registry, Pub/Sub, Cloud SQL, BigQuery, Vertex AI, Storage |
| `suffai-extraction` | Recommended already deployed |
| Cloud SQL | Same instance extraction uses (or dedicated); need `DATABASE_URL` |
| Cloud SQL Auth Proxy + `psql` | For Step 1 migrations (or run SQL in Console) |

```bash
cd AI_CUP_updated
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT
export GCP_REGION=asia-south1
gcloud config set project "$GOOGLE_CLOUD_PROJECT"

# Operational DB (unix socket example)
export CLOUD_SQL_INSTANCE=YOUR_PROJECT:asia-south1:YOUR_INSTANCE
export DATABASE_URL='postgresql://USER:PASSWORD@/DBNAME?host=/cloudsql/'"$CLOUD_SQL_INSTANCE"

export GCS_IMAGE_BUCKET="${GOOGLE_CLOUD_PROJECT}-suffai-garment-images"
export ENABLE_VERTEX=true
export USE_BQ=true
export BQ_DATASET=suffai_ops
```

Make scripts executable once:

```bash
chmod +x deploy/01_prepare_similarity_db.sh \
         deploy/02_deploy_similarity.sh \
         deploy/03_integrate_extraction_pubsub.sh \
         deploy/04_integrate_command_center.sh \
         deploy/05_verify_similarity_e2e.sh \
         deploy/deploy_similarity_e2e.sh \
         deploy/deploy_similarity.sh \
         deploy/deploy_command_center.sh
```

---

## One-shot (all steps)

```bash
./deploy/deploy_similarity_e2e.sh
```

Runs Steps 1 → 5 below.

---

## Step-by-step

### Step 1 — Prepare Cloud SQL + BigQuery

**Script:** [`deploy/01_prepare_similarity_db.sh`](../deploy/01_prepare_similarity_db.sh)

```bash
./deploy/01_prepare_similarity_db.sh
```

Creates / applies:

- Cloud SQL: `003_garment_similarity.sql`, `004_rename_user_wardrobe.sql`  
  (`unique_garments`, `user_wardrobe`, `garment_pin_counts`, `garment_similarity_events`)
- BigQuery: `{BQ_DATASET}.garment_similarity_observations`

---

### Step 2 — Deploy `suffai-similarity`

**Script:** [`deploy/02_deploy_similarity.sh`](../deploy/02_deploy_similarity.sh) → calls [`deploy/deploy_similarity.sh`](../deploy/deploy_similarity.sh)

```bash
./deploy/02_deploy_similarity.sh
```

What it does:

1. Build/push `Dockerfile.similarity`
2. Deploy Cloud Run `suffai-similarity` (`--no-allow-unauthenticated`)
3. Create OIDC push SA + subscription `garment-image-uploaded-similarity-push` on topic `garment-image-uploaded`

Env set: `GOOGLE_CLOUD_PROJECT`, `GCS_IMAGE_BUCKET`, `ENABLE_VERTEX`, `VERTEX_*`, `USE_BQ`, `BQ_DATASET`, optional `DATABASE_URL` / Cloud SQL attach.

---

### Step 3 — Integrate with `suffai-extraction` (Pub/Sub)

**Script:** [`deploy/03_integrate_extraction_pubsub.sh`](../deploy/03_integrate_extraction_pubsub.sh)

```bash
./deploy/03_integrate_extraction_pubsub.sh
```

What it does:

- Ensures shared topic `garment-image-uploaded` exists
- Confirms similarity push sub points at `suffai-similarity`
- Lists extraction’s subscription (does **not** change extraction code or redeploy it)
- Reminds you both services must share `GCS_IMAGE_BUCKET`

Upload contract for similarity: object metadata / path must include `user_uuid` + `pin_code` (see [GARMENT_SIMILARITY.md](GARMENT_SIMILARITY.md)).

---

### Step 4 — Integrate with `suffai-command-center`

**Script:** [`deploy/04_integrate_command_center.sh`](../deploy/04_integrate_command_center.sh)

```bash
./deploy/04_integrate_command_center.sh
```

What it does:

- Resolves `EXTRACTION_URL` + `SIMILARITY_URL` from Cloud Run
- Redeploys command center with both env vars
- Grants CC runtime SA `roles/run.invoker` on extraction + similarity

UI:

| Tab | Backend |
|-----|---------|
| Geo / Ads Lab | BQ + `EXTRACTION_URL` |
| Admin | `SIMILARITY_URL` |

---

### Step 5 — Verify deployment & integration

**Script:** [`deploy/05_verify_similarity_e2e.sh`](../deploy/05_verify_similarity_e2e.sh)

```bash
./deploy/05_verify_similarity_e2e.sh
```

Checks:

| Check | Expected |
|-------|----------|
| Cloud Run services exist | similarity, command-center, extraction |
| `GET {SIM}/health` + ID token | HTTP 200 |
| `GET {SIM}/v1/admin/unique-garments/summary` | 200 (or 503 if DB not attached — script reports INFO) |
| `GET {CC}/health` | `similarity_url: true` |
| `GET {CC}/v1/admin/unique-summary` | 200 (Admin proxy) |
| `GET {CC}/admin.html` | 200 |
| Pub/Sub topic + similarity sub | present |

Exit code `0` = all PASS; non-zero = one or more FAIL.

---

## Manual verification (after scripts)

### A) Health

```bash
REGION=asia-south1
SIM=$(gcloud run services describe suffai-similarity --region=$REGION --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)

curl -sS -H "Authorization: Bearer $TOKEN" "$SIM/health" | jq .
```

Expect: `"service":"suffai-similarity"`, `"enable_vertex":true`, `"use_bq":true`.

### B) Command Center Admin UI

1. Open `{CC_URL}/login.html` — `brand` / `brand123`
2. Open **Admin** tab
3. KPIs load (may be zeros until first uploads)
4. If red error: CC cannot reach similarity (IAM / `SIMILARITY_URL` / DB)

### C) Pub/Sub → both services

Publish a test message (object must exist in GCS with metadata):

```bash
TOPIC=garment-image-uploaded
BUCKET="${GOOGLE_CLOUD_PROJECT}-suffai-garment-images"

# Upload a small test image with metadata first (gsutil):
# gsutil -h "x-goog-meta-user_uuid:test-user-1" -h "x-goog-meta-pin_code:560001" \
#   cp ./sample.jpg gs://$BUCKET/garments/test-user-1/560001/sample.jpg

gcloud pubsub topics publish "$TOPIC" --message="{
  \"gcs_object_key\": \"garments/test-user-1/560001/sample.jpg\",
  \"gcs_bucket\": \"$BUCKET\",
  \"user_uuid\": \"test-user-1\",
  \"pin_code\": \"560001\",
  \"idempotency_key\": \"e2e-test-1\"
}"
```

Then:

```bash
# Similarity logs
gcloud run services logs read suffai-similarity --region=$REGION --limit=30

# Extraction logs (unchanged service — should also receive if its sub is live)
gcloud run services logs read suffai-extraction --region=$REGION --limit=30
```

Look for `[similarity]` outcomes: `created_new` / `user_match` / `skipped_missing_context`.

### D) Direct process API

```bash
curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$SIM/v1/similarity/process" \
  -d "{
    \"gcs_object_key\": \"garments/test-user-1/560001/sample.jpg\",
    \"gcs_bucket\": \"$BUCKET\",
    \"user_uuid\": \"test-user-1\",
    \"pin_code\": \"560001\",
    \"idempotency_key\": \"e2e-direct-1\"
  }" | jq .
```

### E) Ads Lab still uses extraction only

```bash
CC=$(gcloud run services describe suffai-command-center --region=$REGION --format='value(status.url)')
curl -sS "$CC/health" | jq '{extraction_url, similarity_url, use_bq}'
```

Both flags should be `true` after Step 4.

---

## Script index

| Script | Role |
|--------|------|
| [`deploy/deploy_similarity_e2e.sh`](../deploy/deploy_similarity_e2e.sh) | Run all steps |
| [`deploy/01_prepare_similarity_db.sh`](../deploy/01_prepare_similarity_db.sh) | SQL + BQ DDL |
| [`deploy/02_deploy_similarity.sh`](../deploy/02_deploy_similarity.sh) | Deploy similarity + Pub/Sub push |
| [`deploy/03_integrate_extraction_pubsub.sh`](../deploy/03_integrate_extraction_pubsub.sh) | Verify shared topic / subs |
| [`deploy/04_integrate_command_center.sh`](../deploy/04_integrate_command_center.sh) | Wire CC env + IAM |
| [`deploy/05_verify_similarity_e2e.sh`](../deploy/05_verify_similarity_e2e.sh) | Automated checks |
| [`deploy/deploy_similarity.sh`](../deploy/deploy_similarity.sh) | Low-level similarity deploy |
| [`deploy/deploy_command_center.sh`](../deploy/deploy_command_center.sh) | Low-level CC deploy |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `/health` 403 on similarity | Need Bearer ID token; service is private |
| Admin proxy 403/503 | Grant CC SA `run.invoker` on similarity (Step 4 does this) |
| Summary 503 / DB errors | Set `DATABASE_URL` + `--add-cloudsql-instances` on similarity |
| `skipped_missing_context` | Add `user_uuid` + `pin_code` metadata/path |
| Extraction not running | Separate issue — check extraction push sub; similarity does not replace it |
| BQ insert errors | Create table (Step 1); grant BigQuery Data Editor on runtime SA |

---

## IAM checklist (runtime SA for similarity)

- `roles/storage.objectViewer` on image bucket  
- `roles/aiplatform.user`  
- Cloud SQL Client  
- `roles/bigquery.dataEditor` (if `USE_BQ=true`)  
- Pub/Sub push SA → `roles/run.invoker` on `suffai-similarity`  
- Command center SA → `roles/run.invoker` on `suffai-similarity` and `suffai-extraction`  
