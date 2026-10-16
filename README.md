# Suffai — Fashion Intelligence (GCP)

Multi-tenant B2B fashion demand intelligence on **Google Cloud**.  
**`suffai-api` has been removed** — the BFF/UI surface is **`suffai-command-center`**.

| Unit | Cloud Run service | Stack |
|------|-------------------|--------|
| Command Center (UI + BFF) | `suffai-command-center` | FastAPI + static `web/` (Geo, Ads Lab, chat) |
| Extraction worker | `suffai-extraction` | FastAPI Pub/Sub push consumer (`worker/`) |
| Similarity | `suffai-similarity` | Unique garments / wardrobe / brand SKU compare |
| Ops DB | Cloud SQL PostgreSQL | `db/migrations/` |
| Analytics | BigQuery | `command_center` metrics + optional `analytics/bigquery/` |
| Images | GCS | private buckets |
| Events | Pub/Sub | garment upload → extraction (+ similarity) |

## Architecture

```
Browser → suffai-command-center (Cloud Run)
            ├─ /v1/metrics (BigQuery)
            ├─ /v1/ads-lab/campaign|extract → GCS + extraction + similarity
            └─ /v1/chat (Vertex)
GCS upload → Pub/Sub → suffai-extraction
                     → suffai-similarity (wardrobe path)
```

## Local

```bash
# Command Center
pip install -r command_center/requirements.txt
USE_BQ=true GOOGLE_CLOUD_PROJECT=… uvicorn command_center.main:app --host 0.0.0.0 --port 8083
# → http://127.0.0.1:8083/login.html

# Or Docker Compose (worker + command_center + similarity)
docker compose up --build
```

Demo login: `brand` / `brand123` (see `web/login.html`).

## Deploy to GCP

Primary docs: [docs/GCP_COMMAND_CENTER.md](docs/GCP_COMMAND_CENTER.md)

```bash
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT
export GCP_REGION=asia-south1
chmod +x deploy/deploy_cloud_run.sh deploy/deploy_command_center.sh
./deploy/deploy_cloud_run.sh
# or: ./deploy/deploy_command_center.sh
```

## Repository layout

```
command_center/      FastAPI BFF (metrics, Ads Lab, chat)
web/                 Static HTML UI (login, geo, ads_lab, admin)
worker/              Extraction Pub/Sub worker → Cloud Run suffai-extraction
similarity/          Similarity / wardrobe / compare-pins
command_center_data/ Pin population + sample CSVs for BQ load
db/migrations/       Cloud SQL (wardrobe + brand SKU campaigns)
deploy/              Deploy scripts (no suffai-api)
Dockerfile.command_center / Dockerfile.worker / Dockerfile.similarity
```

Legacy packages (`suffai/`, `services/`, `jobs/`, `archive/`) may remain for offline jobs/tests but are **not** deployed as `suffai-api`.

## Environment (Command Center)

| Variable | Purpose |
|----------|---------|
| `USE_BQ` / `BQ_DATASET` | Metrics from BigQuery |
| `GCS_IMAGE_BUCKET` | Image uploads |
| `EXTRACTION_URL` | Cloud Run URL of `suffai-extraction` |
| `SIMILARITY_URL` | Cloud Run URL of `suffai-similarity` |
| `DATABASE_URL` | Cloud SQL (campaigns, job counts) |
| `ENABLE_VERTEX` | Chat + (similarity) embeddings |

See [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) for older notes; treat `suffai-api` mentions there as historical.
