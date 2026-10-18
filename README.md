# Suffai — Fashion Intelligence (GCP)

Multi-tenant B2B fashion demand intelligence on **Google Cloud**.  
**Streamlit is not part of the runtime** (legacy demos live under `archive/streamlit/`).

| Unit | Cloud Run service | Stack |
|------|-------------------|--------|
| Web | `suffai-web` | Next.js 14 |
| API / BFF | `suffai-api` | FastAPI + governed `suffai` core |
| Extraction worker | `suffai-extraction` | FastAPI Pub/Sub push consumer |
| Ops DB | Cloud SQL PostgreSQL | `db/migrations/` |
| Analytics | BigQuery | `analytics/bigquery/` |
| Images / raw | GCS | private buckets |
| Events | Pub/Sub + DLQ | garment upload |

## Architecture

```
Next.js (Cloud Run) → FastAPI BFF (Cloud Run)
                         ├─ Cloud SQL (garments, consent, tenants, campaigns)
                         ├─ BigQuery marts (geo, RFM, brand aggregates)
                         ├─ GCS signed uploads
                         └─ Vertex Gemini (opt-in)
GCS upload event → Pub/Sub → extraction worker (Cloud Run)
```

Laundry service quantities are **not** garment purchases. Brand share requires verified consented garment observations.

## Local GCP-parity

```bash
# API
python3 -m pip install -r requirements.txt --index-url https://pypi.org/simple
PYTHONPATH=. uvicorn api.main:app --host 0.0.0.0 --port 8080

# Web (needs Node 20+)
cd web && npm install && NEXT_PUBLIC_API_URL=http://localhost:8080 npm run dev

# Or full stack via Docker
docker compose up --build
# API http://localhost:8080/docs  ·  Web http://localhost:8081
```

Demo brand login: any listed brand · password `brand123`.

## Tests

```bash
PYTHONPATH=. python3 -m pytest tests/ -q   # includes the frontend suite when Node 20+ is installed
node --test tests/frontend/                 # frontend only (web/ pages, no npm install needed)
python3 scripts/run_integrity_report.py
```

## Deploy to GCP

**Step-by-step (recommended):** [docs/GCP_DEPLOY_STEP_BY_STEP.md](docs/GCP_DEPLOY_STEP_BY_STEP.md) — Blueprint-aligned phases (Cloud Run units, Cloud SQL, BigQuery, Pub/Sub, Scheduler, Identity Platform).

**Console UI detail / troubleshooting:** [docs/GCP_CONSOLE_DEPLOY_AND_TEST.md](docs/GCP_CONSOLE_DEPLOY_AND_TEST.md).

1. Confirm **project ID, region/residency, billing** (required before apply).
2. Copy `deploy/env.example` → set secrets in Secret Manager (not git).
3. Terraform (gated):

```bash
cd infra/terraform
terraform init
terraform plan -var="project_id=YOUR_PROJECT" -var="enable_resources=false"
# Only after approval:
terraform apply -var="project_id=YOUR_PROJECT" -var="enable_resources=true"
```

4. Build/deploy services:

```bash
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT
export GCP_REGION=asia-south1
chmod +x deploy/deploy_cloud_run.sh
./deploy/deploy_cloud_run.sh
```

Or use `cloudbuild.yaml` with Artifact Registry.

## Repository layout

```
api/                 FastAPI Cloud Run BFF
web/                 Next.js Cloud Run dashboard
worker/              Extraction Pub/Sub worker
suffai/              Governed ingest + metrics + privacy + garments
config/settings.py   GCP env contract
db/migrations/       Cloud SQL
analytics/bigquery/  Warehouse DDL
infra/terraform/     IAM, GCS, BQ, Pub/Sub, Cloud Run, SQL
contracts/openapi.yaml
deploy/              env example + deploy script
archive/streamlit/   Deprecated local Streamlit prototypes
```

## Environment (Cloud Run)

| Variable | Purpose |
|----------|---------|
| `PORT` | Cloud Run port (8080) |
| `WEB_ORIGIN` | CORS allowlist (web service URL) |
| `DATA_DIR` / `GCS_BUCKET` | Fixture path or GCS sync |
| `DATABASE_URL` / `CLOUD_SQL_INSTANCE` | Operational PostgreSQL |
| `BQ_PROJECT` | BigQuery project |
| `JWT_SECRET` | Demo auth (replace with Identity Platform) |
| `ENABLE_VERTEX` | Extraction paid API latch (default false) |

See [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) and [docs/PRD_COVERAGE.md](docs/PRD_COVERAGE.md).

## PRD features (v2.1) — redeploy note

Existing geo / auth / segments / chat contracts are unchanged. New additive APIs power Capture, Review, Brand catalog, Research, Campaign Lab, and CSV export.

Redeploy **`suffai-api`** and **`suffai-web`** only (worker already has GCS→Vertex→SQL). Optional env: `EXTRACTION_WORKER_URL`, `GCS_IMAGE_BUCKET`, `PUBSUB_IMAGE_TOPIC`.
