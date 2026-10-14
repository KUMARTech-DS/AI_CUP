# PRD coverage (v2.2)

| ID | Feature | Implementation |
|----|---------|----------------|
| ING-01 | Fixture ingest + integrity | `suffai/ingest/*`, `GET /v1/integrity/report` |
| SEG-01 | RFM | `suffai/metrics/rfm.py`, `GET /v1/segments/summary` |
| GEO-01 | Geo aggregates + k-anon + choropleth | `POST /v1/geo/query`, `GET /v1/geo/choropleth`, `GET /v1/geo/polygons`, web `/geo` + SVG map |
| IMG-01 | Consent + signed/local upload | `POST /v1/garments/consent`, `images:initiate`, `upload-local`, web `/capture` |
| IMG-02 | Extraction + review | `POST /v1/garments/extractions`, worker GCS→Vertex→SQL, web `/review` |
| DAT-01 | Garment facts + corrections | `GarmentStore` + Cloud SQL dual-write + `PATCH /v1/garments/{id}/attributes` |
| BRD-01 | Brand benchmarks | `GET /v1/brands/{id}/benchmarks` + catalog endpoints |
| RES-01 | Product opportunities | `POST /v1/product-opportunities`, web `/research` |
| CMP-01 | Campaign fit | `POST /v1/campaigns`, `/{id}/evaluate`, web `/campaigns` |
| SKU-RANK v1.2 | SKU ranking (1–20 images × PIN) | `POST /api/v1/sku/rank`, `GET /api/v1/sku/areas` (`api/routers/sku_rank.py`), scorer `suffai/metrics/sku_rank.py`, images `suffai/garments/sku_vision.py` (ephemeral, Vertex inline), web `/ads-lab` |
| CHAT-01 | Governed chat | `POST /v1/chat/query`, web `/chat` |
| EXP-01 | CSV export + saved views + CDC schedule | `GET /v1/exports/geo.csv` (role-gated), saved views, `GET /v1/activation/snapshots/schedule-status`, Cloud Scheduler jobs |
| ADV-01 | Conversion / uplift / media / inventory | `api/routers/activation.py`, web `/activation` (observational uplift — not causal claim) |
| AUTH | Identity Platform + demo JWT | `services/identity_platform.py`, `POST /v1/auth/identity/session`, web Identity Toolkit login |
| PRIV | k-anon + differencing | `suffai/privacy/suppress.py`, `differencing.py` (hashed cohorts in staging/prod) |
| BQ | Warehouse sync + marts read | `jobs/bq_sync.py`, `jobs/cdc_ingest.py`, `USE_BIGQUERY_MARTS` → `suffai/warehouse/bq_reader.py` |
| SQL | Cloud SQL ops | `db/migrations/001_*.sql`, `002_adv_privacy_ops.sql`, `services/ops_db.py` |

**Redeploy note:** Redeploy **`suffai-api`**, **`suffai-web`**, and (for CDC/BQ) build **`suffai-job`** via `deploy/cloudbuild-job.yaml`. Wire Scheduler from `deploy/scheduler.yaml` / `infra/terraform/modules/scheduler`.

**GCP-native stack:** Cloud Run (api/web/extraction) · Cloud Run Jobs (bq/cdc) · Cloud Scheduler · GCS · Pub/Sub · Vertex AI · Cloud SQL · BigQuery · Identity Platform.
