# Command Center — quick steps

Full GCP API list + deploy: **[GCP_COMMAND_CENTER.md](GCP_COMMAND_CENTER.md)**  
Similarity / unique garments: **[GARMENT_SIMILARITY.md](GARMENT_SIMILARITY.md)**  
E2E deploy + extraction/CC integration: **[DEPLOY_SIMILARITY_E2E.md](DEPLOY_SIMILARITY_E2E.md)**

## Layout

| Folder | Role |
|--------|------|
| `web/` | UI (login, Geo, Ads Lab, Admin) |
| `command_center/` | BFF APIs (does not change `api/` or `worker/`) |
| `similarity/` | Unique-garment similarity (independent Cloud Run) |
| `command_center_data/` | Sample CSVs + pin population (**load into BQ**; not runtime fallback) |

## Data path

**GCP-native only:**

- Geo / Ads Lab filters → `GET /v1/metrics` → **BigQuery** (`USE_BQ=true`)
- Ads Lab upload → `POST /v1/ads-lab/extract` → GCS → existing `suffai-extraction`
- Ads Lab **Score** = dummy random 0–100 in the browser (CTR removed)
- Admin → `GET /v1/admin/unique-summary` → `suffai-similarity`

No `metrics.json` or local CSV fallback. Failures show HTTP / UI errors — check Cloud Run logs (`[command_center]` / `[similarity]`).

## Local (needs BQ + ADC)

```bash
cd AI_CUP_updated
export USE_BQ=true GOOGLE_CLOUD_PROJECT=YOUR_PROJECT BQ_DATASET=suffai_ops
export SIMILARITY_URL=http://127.0.0.1:8084
gcloud auth application-default login
pip install -r command_center/requirements.txt
uvicorn command_center.main:app --host 127.0.0.1 --port 8083
# http://127.0.0.1:8083/login.html   brand / brand123
```

## BQ tables

```bash
./command_center/scripts/load_to_bq.sh YOUR_PROJECT ./command_center_data/sample
```

## Deploy

```bash
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT
export GCP_REGION=asia-south1
export EXTRACTION_URL=$(gcloud run services describe suffai-extraction \
  --region=$GCP_REGION --format='value(status.url)')
export SIMILARITY_URL=$(gcloud run services describe suffai-similarity \
  --region=$GCP_REGION --format='value(status.url)')
./deploy/deploy_command_center.sh
```
