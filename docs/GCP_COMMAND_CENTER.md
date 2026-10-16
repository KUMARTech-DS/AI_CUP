# Command Center — layout, APIs, deploy

## Answers

1. **UI lives in `web/`** — static HTML (login, Geo, Ads Lab), not the old Next.js app.  
2. **GCP-native metrics:** UI calls `GET /v1/metrics` every load. Data comes **only from BigQuery**. There is **no** local CSV or `metrics.json` fallback — failures return HTTP errors so you can fix BQ/IAM.  
3. **All of this is under `AI_CUP_updated/`** (for git push / deploy).  
4. **`suffai-api` is retired.** BFF is `command_center/` on Cloud Run `suffai-command-center`. Extraction (`worker/`) and similarity remain separate.

---

## Repo layout

```text
AI_CUP_updated/
  web/                      # UI (login.html, index.html, geo.html, ads_lab.html, config.js)
  command_center/           # FastAPI BFF (metrics + extract proxy)
    main.py
    requirements.txt
    scripts/                # aggregate_metrics.py, load_to_bq.sh, create_bq_tables.sh
  command_center_data/      # pin_population.json + sample CSVs (load into BQ only)
  Dockerfile.command_center
  deploy/deploy_command_center.sh
  deploy/cloudbuild-command-center.yaml
```

`_new_approach/` has been **removed** (content folded into `web/` + `command_center/`).

---

## APIs on Cloud Run service `suffai-command-center`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Health + env flags |
| `GET` | `/v1/metrics` | Geo aggregates from **BigQuery only** (+ optional `extraction_jobs_count`) |
| `POST` | `/v1/ads-lab/extract` | Upload → GCS `demo/` → existing `suffai-extraction` `/v1/extract` (baseline extract; still available) |
| `POST` | `/v1/ads-lab/campaign` | **Primary Ads Lab path:** brand SKU campaign — GCS `brands/{brand}/…` → extract (brand overwrite) → similarity compare-pins → rank with PIN+age+gender+RFM |
| `POST` | `/v1/chat` | Corner bot on Command Center (not legacy `api/`): Vertex Gemini + slim `web/metrics.json` + Geo/Ads UI + optional `filter_context` |
| `GET` | `/v1/admin/unique-summary` | Admin tab proxy → `suffai-similarity` `/v1/admin/unique-garments/summary` |
| `GET` | `/v1/admin/unique-garments` | Admin tab proxy → `suffai-similarity` `/v1/admin/unique-garments` |
| static | `/login.html`, `/index.html`, `/geo.html`, `/ads_lab.html`, `/admin.html`, `/metrics.json` | UI |

**Unchanged:** `suffai-extraction` → `POST /v1/extract` (Vertex-enabled in your env).

**Combined surface (baseline + Feature 1):** Geo UI + Ads Lab campaign ranking + corner Bot chat + legacy extract endpoint. Do **not** overwrite `command_center/` or `web/` from the `temp/` archive (pre–Feature 1 baseline). Chat lives on CC `POST /v1/chat` only — the retired `suffai-api` / `api/routers/chat.py` path is not restored.

**Chat UI:** floating corner **Bot** on `index.html` (Geo + Ads Lab only; hidden on Admin). Uses `web/metrics.json` summary on the server. Request body may include optional `filter_context` (PIN/age/gender/RFM/brand) merged into the Vertex prompt.

**Admin tab:** `admin.html` in the shell calls CC `/v1/admin/*`, which proxies to similarity (requires `SIMILARITY_URL` + CC SA `run.invoker` on similarity).

**Ads Lab campaign (Part 1):** brand name + multi-PIN + age/gender/RFM → `POST /v1/ads-lab/campaign`. Extraction service unchanged; brand overwritten in BFF. Ranking blends PIN unique-garment similarity (`suffai-similarity` `/v1/similarity/compare-pins`) with metrics taste for the full audience. Tables: `brand_campaigns`, `brand_skus`, `brand_sku_pin_scores` (migration `005_brand_sku_campaigns.sql`). Requires `SIMILARITY_URL` + `DATABASE_URL` on command-center.

---

## Errors you will see (no silent fallback)

| Cause | Response |
|--------|----------|
| `USE_BQ=false` | `503` — `BigQuery required: set USE_BQ=true` |
| Project unset | `503` — `GOOGLE_CLOUD_PROJECT is not set` |
| BQ tables / IAM fail | `503` — `BigQuery metrics failed (check tables/IAM): …` |
| Geo UI | Full-page error with HTTP detail |
| Ads Lab filters | Red message under target areas |

Cloud Run logs use prefix `[command_center]`.

---

## Deploy (GCP)

Prerequisites: `gcloud` authenticated, Docker running, billing enabled, Artifact Registry + Cloud Run + BigQuery APIs. Prefer region `asia-south1`.

### Step 1 — Project & region

```bash
cd AI_CUP_updated

export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT
export GCP_REGION=asia-south1
gcloud config set project "$GOOGLE_CLOUD_PROJECT"
```

### Step 2 — Load BigQuery tables (required for Geo)

Sample CSVs are for **loading into BQ only**, not runtime fallback:

```bash
chmod +x command_center/scripts/load_to_bq.sh
./command_center/scripts/load_to_bq.sh "$GOOGLE_CLOUD_PROJECT" ./command_center_data/sample
```

Cloud Run service account needs BigQuery Job User + Data Viewer (or equivalent) on dataset `suffai_ops`.

### Step 3 — Point Ads Lab extract at extraction service

```bash
export EXTRACTION_URL=$(gcloud run services describe suffai-extraction \
  --region="$GCP_REGION" \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)')
```

### Step 4 — Build & deploy `suffai-command-center`

```bash
chmod +x deploy/deploy_command_center.sh
./deploy/deploy_command_center.sh
```

Env set by the script (defaults):

- `USE_BQ=true`
- `BQ_DATASET=suffai_ops`
- `GOOGLE_CLOUD_PROJECT`
- `GCS_IMAGE_BUCKET` (default `${PROJECT}-suffai-garment-images`)
- `EXTRACTION_URL` (if set)
- `CORS_ORIGINS=*`
- `ENABLE_VERTEX=true` (required for `/v1/chat`)
- `VERTEX_LOCATION=asia-south1`
- `VERTEX_CHAT_MODEL=gemini-2.0-flash`

Cloud Run SA needs **Vertex AI User** for chat.

### Step 5 — (Optional) Cloud SQL for `extraction_jobs_count`

Attach Cloud SQL and set `DATABASE_URL` via Secret Manager. Without it, Geo still works; job count is omitted.

### Step 6 — Verify

```bash
URL=$(gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)')

curl -sS "$URL/health"
curl -sS "$URL/v1/metrics" | head
# Chat (needs ENABLE_VERTEX=true + web/metrics.json):
curl -sS -X POST "$URL/v1/chat" -H 'Content-Type: application/json' \
  -d '{"message":"Summarize top brands","context":{"active_tab":"geo"}}'
# Open: $URL/login.html   brand / brand123 — corner Bot on Geo/Ads
```

### Alternative — Cloud Build

```bash
gcloud builds submit --config=deploy/cloudbuild-command-center.yaml .
```

---

## Local (still needs BigQuery)

```bash
cd AI_CUP_updated
export USE_BQ=true
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT
export BQ_DATASET=suffai_ops
# ADC: gcloud auth application-default login

pip install -r command_center/requirements.txt
uvicorn command_center.main:app --host 127.0.0.1 --port 8083
# → http://127.0.0.1:8083/login.html
```

Or: `docker compose up --build command_center` (with the same env).

---

## Notes

- Does **not** replace `suffai-extraction` or `suffai-similarity`.  
- Pub/Sub + Scheduler CDC / BQ sync jobs are part of the wider stack (`deploy/scheduler.yaml`); leave them as configured.  
- Old Next.js UI under `web/` has been removed; `web/` is HTML only.
