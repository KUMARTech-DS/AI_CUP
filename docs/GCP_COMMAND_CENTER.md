# Command Center — layout, APIs, deploy

## Answers

1. **UI lives in `web/`** — static HTML (login, Geo, Ads Lab), not the old Next.js app.  
2. **GCP-native metrics:** UI calls `GET /v1/metrics` every load. Data comes **only from BigQuery**. There is **no** local CSV or `metrics.json` fallback — failures return HTTP errors so you can fix BQ/IAM.  
3. **All of this is under `AI_CUP_updated/`** (for git push / deploy).  
4. **Existing `api/`, `worker/`, `suffai/` source is unchanged.** New surface is `command_center/` + Cloud Run `suffai-command-center`.

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
| `POST` | `/v1/ads-lab/extract` | Upload → GCS `demo/` → existing `suffai-extraction` `/v1/extract` |
| static | `/login.html`, `/index.html`, `/geo.html`, `/ads_lab.html` | UI |

**Unchanged:** `suffai-extraction` → `POST /v1/extract` (Vertex-enabled in your env).

**Ads Lab score:** UI shows a **dummy score 0–100** (random in browser). CTR column removed. Ranking is not a live GCP model yet.

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

### Step 5 — (Optional) Cloud SQL for `extraction_jobs_count`

Attach Cloud SQL and set `DATABASE_URL` via Secret Manager. Without it, Geo still works; job count is omitted.

### Step 6 — Verify

```bash
URL=$(gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)')

curl -sS "$URL/health"
curl -sS "$URL/v1/metrics" | head
# Open: $URL/login.html   brand / brand123
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

- Does **not** replace `suffai-api` or `suffai-extraction`.  
- Pub/Sub + Scheduler CDC / BQ sync jobs are part of the wider stack (`deploy/scheduler.yaml`); leave them as configured.  
- Old Next.js UI under `web/` has been removed; `web/` is HTML only.
