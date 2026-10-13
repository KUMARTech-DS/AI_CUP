# Suffai — Step-by-step GCP deployment guide

Aligned with `Suffai_GCP_Engineering_Blueprint.md` (§1 architecture, §2 GCP mapping, §11 environments).  
Use this as the operator runbook from empty project → working dashboards.

> **Confirm before you spend money:** GCP project, region/residency, billing.  
> Default region in this guide: **`asia-south1`**. Change every occurrence if residency differs.  
> Keep **`ENABLE_VERTEX=false`** until you accept Vertex AI Gemini cost.

---

## Blueprint → repo mapping

| Blueprint unit (§1) | GCP service | Exact name in this repo |
|---|---|---|
| Next.js dashboard | Cloud Run | `suffai-web` |
| Cloud Run API / BFF | Cloud Run | `suffai-api` |
| Cloud Run extraction worker | Cloud Run | `suffai-extraction` |
| Cloud Run ingestion jobs | Cloud Run Job | `suffai-cdc` |
| BigQuery segmentation / aggregate jobs | Cloud Run Job | `suffai-bq-sync` |
| Cloud SQL PostgreSQL | Cloud SQL | instance `suffai-ops` |
| BigQuery raw + curated | BigQuery datasets | `suffai_raw`, `suffai_staging`, `suffai_marts` |
| GCS raw immutable snapshots | Cloud Storage | `{PROJECT_ID}-suffai-raw` |
| Private GCS garment images | Cloud Storage | `{PROJECT_ID}-suffai-garment-images` |
| Pub/Sub image event | Pub/Sub | topic `garment-image-uploaded` (+ `-dlq`) |
| Cloud Scheduler (+ Workflows) | Cloud Scheduler | `suffai-cdc-hourly`, `suffai-bq-marts-daily` |
| Identity Platform | Identity Platform | project-level Email/Password (or federated IdP) |
| Secret Manager + Cloud KMS | Secret Manager | `suffai-jwt-secret` |
| CI/CD | Cloud Build + Artifact Registry | repo `suffai` |
| Observability | Cloud Logging / Monitoring | Cloud Run + Job logs |
| Environments (§11) | GCP projects | `suffai-dev` → `suffai-staging` → `suffai-prod` |

**Docker / build sources**

| Image | Dockerfile | Cloud Build config |
|---|---|---|
| `suffai-api` | `Dockerfile.api` | `deploy/cloudbuild-api.yaml` |
| `suffai-web` | `Dockerfile.web` | `deploy/cloudbuild-web.yaml` |
| `suffai-extraction` | `Dockerfile.worker` | `deploy/cloudbuild-worker.yaml` |
| `suffai-job` (ingestion + analytics) | `Dockerfile.job` | `deploy/cloudbuild-job.yaml` |

Env reference: `deploy/env.example` · Runtime mapping: `config/settings.py`.

---

## Phase A — Project foundation (do once per environment)

### A1. Create the GCP project

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. **Select a project** → **New project**.
3. Name/ID pattern from Blueprint §11:
   - Dev: `suffai-dev`
   - Staging: `suffai-staging`
   - Prod: `suffai-prod`
4. Link **Billing**.
5. Note **Project ID** → substitute `{PROJECT_ID}` everywhere below.
6. Set default region (Cloud Shell / local):

```bash
gcloud config set project {PROJECT_ID}
gcloud config set run/region asia-south1
export GOOGLE_CLOUD_PROJECT={PROJECT_ID}
export GCP_REGION=asia-south1
```

### A2. Enable APIs (Blueprint §2)

**APIs & Services → Library** — enable:

| API | Used for |
|---|---|
| Cloud Run API | `suffai-web`, `suffai-api`, `suffai-extraction`, Jobs |
| Artifact Registry API | Docker images in repo `suffai` |
| Cloud Build API | Image builds |
| Secret Manager API | `JWT_SECRET` and later DB passwords |
| Cloud Logging API / Cloud Monitoring API | Observability |
| Cloud Storage API | Raw + garment image buckets |
| BigQuery API | `suffai_raw` / `suffai_staging` / `suffai_marts` |
| Pub/Sub API | Garment upload events + DLQ |
| Cloud SQL Admin API | PostgreSQL `suffai-ops` |
| Identity Toolkit / Identity Platform | User login |
| Cloud Scheduler API | Hourly CDC / daily marts |
| IAM API | Service accounts |
| Vertex AI API | Gemini extraction (**only when ready to pay**) |
| Workflows API | Optional orchestration wrapper (Blueprint §1) |

### A3. Artifact Registry

**Artifact Registry → Create repository**

| Field | Value |
|---|---|
| Name | `suffai` |
| Format | Docker |
| Region | `asia-south1` |

Images will be:

```text
asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-api:latest
asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-web:latest
asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-extraction:latest
asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-job:latest
```

### A4. Secret Manager

**Secret Manager → Create secret**

| Field | Value |
|---|---|
| Name | `suffai-jwt-secret` |
| Value | Long random string (not `dev-only-change-me`) |

Later grant the Cloud Run runtime service account for `suffai-api`:  
**Secret Manager Secret Accessor** on `suffai-jwt-secret`.

### A5. Service accounts (Blueprint §11 — separate principals)

Create (IAM → Service accounts), minimum set:

| SA email prefix | Used by | Typical roles (start minimal) |
|---|---|---|
| `suffai-api-sa` | Cloud Run `suffai-api` | Secret Accessor; Cloud SQL Client; BQ Job User + Data Viewer on marts; Storage Object Viewer/Creator on image bucket |
| `suffai-web-sa` | Cloud Run `suffai-web` | (usually none beyond run.invoker if private) |
| `suffai-extraction-sa` | Cloud Run `suffai-extraction` | Storage Object Viewer on images; Vertex AI User (when enabled); Cloud SQL Client; Pub/Sub Subscriber |
| `suffai-ingestion-sa` | Job `suffai-cdc` | Storage Admin on raw bucket; BigQuery Data Editor; (Mongo secret access) |
| `suffai-analytics-sa` | Job `suffai-bq-sync` | BigQuery Job User + Data Editor on raw/staging/marts |
| `suffai-scheduler-sa` | Cloud Scheduler | Cloud Run Invoker / Job Runner on the two Jobs |

Do **not** grant project Editor to shared humans or runtime SAs.

---

## Phase B — Minimum demo (fixtures only)

Goal: Geo / RFM / Ask Suffai / Brand empty-states working with baked-in `data_offline/` — **no** Cloud SQL, Vertex, or Mongo required.

### B1. Build & deploy `suffai-api` (Cloud Run API / BFF)

**Option — Cloud Build (recommended)**

From repo root:

```bash
gcloud builds submit --config=deploy/cloudbuild-api.yaml
```

**Option — local Docker + push**

```bash
./deploy/deploy_cloud_run.sh   # builds api+web+worker; see script
```

**Cloud Run → Create service** (if not done by script)

| Field | Value |
|---|---|
| Service name | `suffai-api` |
| Region | `asia-south1` |
| Container image | `…/suffai/suffai-api:latest` |
| Port | `8080` |
| Authentication | Allow unauthenticated **or** IAP later |
| Memory / CPU | 1 Gi / 1 |
| Runtime SA | `suffai-api-sa@…` |

**Environment variables (minimum demo)**

| Name | Value |
|---|---|
| `ENVIRONMENT` | `staging` (or `prod`) |
| `PORT` | `8080` |
| `DEMO_AUTH_ENABLED` | `true` |
| `GOOGLE_CLOUD_PROJECT` | `{PROJECT_ID}` |
| `GCP_REGION` | `asia-south1` |
| `JWT_SECRET` | from Secret Manager reference `suffai-jwt-secret` |
| `PRIVACY_K` | `30` |
| `PRIVACY_DIFFERENCING_ENABLED` | `true` |
| `ENABLE_ADV` | `true` |
| `ENABLE_VERTEX` | `false` |
| `USE_BIGQUERY_MARTS` | `false` (until Phase D) |
| `IDENTITY_PLATFORM_ENABLED` | `false` (until Phase E) |

Copy the service URL → `{API_URL}`  
Smoke: open `{API_URL}/health` → `"status":"ok"`.

### B2. Build & deploy `suffai-web` (Next.js on Cloud Run)

Web **must** be built with the API URL baked in:

```bash
gcloud builds submit --config=deploy/cloudbuild-web.yaml \
  --substitutions=_API_URL={API_URL}
```

(`deploy/cloudbuild-web.yaml` passes `_API_URL` into Docker as `NEXT_PUBLIC_API_URL`.)

| Field | Value |
|---|---|
| Service name | `suffai-web` |
| Region | `asia-south1` |
| Image | `…/suffai/suffai-web:latest` |
| Port | `8080` |
| Auth | Allow unauthenticated for demo |

Copy URL → `{WEB_URL}`.

### B3. Point API CORS at the web origin

Update Cloud Run `suffai-api`:

| Name | Value |
|---|---|
| `WEB_ORIGIN` | `{WEB_URL}` |
| `API_PUBLIC_URL` | `{API_URL}` |

### B4. Deploy `suffai-extraction` (Cloud Run extraction worker)

Even if you are not uploading images yet, deploy so Pub/Sub can be wired later.

```bash
gcloud builds submit --config=deploy/cloudbuild-worker.yaml
```

| Field | Value |
|---|---|
| Service name | `suffai-extraction` |
| Region | `asia-south1` |
| Auth | **Require authentication** (Pub/Sub OIDC only) |
| Env | `ENABLE_VERTEX=false`, `GOOGLE_CLOUD_PROJECT={PROJECT_ID}` |

### B5. Browser acceptance (Phase B gate)

1. Open `{WEB_URL}/login`
2. Brand: `Peter England` · Password: `brand123`
3. Verify pages load without API CORS errors:
   - **Geo Intelligence** (`/geo`) — tiles + choropleth
   - **Ask Suffai** (`/chat`) — e.g. “popular commodities in 560103”
   - **Brand / Research / Campaigns** — empty-state or fixture-backed as designed
4. API docs: `{API_URL}/docs`

**Gate:** Geo + RFM + chat work; login works; no Streamlit involved.

---

## Phase C — Cloud Storage, Pub/Sub, Cloud SQL (operational path)

Blueprint: private GCS garment images → Pub/Sub → extraction worker → Cloud SQL PostgreSQL.

### C1. Cloud Storage buckets

**Cloud Storage → Create**

| Bucket | Purpose | Notes |
|---|---|---|
| `{PROJECT_ID}-suffai-raw` | Raw immutable snapshots | Uniform bucket-level access; no public |
| `{PROJECT_ID}-suffai-garment-images` | Private garment images | No public; signed URL upload only |

Set on `suffai-api` / worker:

| Name | Value |
|---|---|
| `GCS_RAW_BUCKET` / `GCS_BUCKET` | `{PROJECT_ID}-suffai-raw` |
| `GCS_IMAGE_BUCKET` | `{PROJECT_ID}-suffai-garment-images` |

### C2. Pub/Sub (upload-ready event)

**Pub/Sub → Create topics**

| Topic | Purpose |
|---|---|
| `garment-image-uploaded` | Primary event |
| `garment-image-uploaded-dlq` | Dead letter |

**Subscription** `garment-image-uploaded-push`:

| Field | Value |
|---|---|
| Delivery | Push |
| Endpoint | `{EXTRACTION_URL}/` (Cloud Run `suffai-extraction` URL) |
| Auth | Enable OIDC → SA with **Cloud Run Invoker** on `suffai-extraction` |
| Dead letter topic | `garment-image-uploaded-dlq` |
| Max delivery attempts | `5` |

Env on API/worker: `PUBSUB_IMAGE_TOPIC=garment-image-uploaded`.

### C3. Cloud SQL PostgreSQL (`suffai-ops`)

1. **SQL → Create instance** → PostgreSQL  
   - Instance ID: `suffai-ops`  
   - Region: `asia-south1`  
   - Prefer private IP + VPC connector for Cloud Run (Blueprint §11); public IP only for short demos.
2. Create database `suffai` and user; store password in Secret Manager (e.g. `suffai-db-password`).
3. Apply migrations (Cloud SQL Studio or `psql`):

```text
db/migrations/001_operational.sql
db/migrations/002_adv_privacy_ops.sql
```

4. Attach Cloud SQL to Cloud Run services `suffai-api` and `suffai-extraction` (**Connections → Cloud SQL**).
5. Set:

| Name | Value |
|---|---|
| `CLOUD_SQL_INSTANCE` | `{PROJECT_ID}:asia-south1:suffai-ops` |
| `DATABASE_URL` | `postgresql://USER:PASSWORD@/suffai?host=/cloudsql/{PROJECT_ID}:asia-south1:suffai-ops` |

`suffai-api` bootstraps ADV/ops tables on startup when `DATABASE_URL` is set; worker persists extractions via `worker/db.py`.

### C4. Vertex AI Gemini (optional paid)

Only after cost approval:

1. Enable **Vertex AI API**.
2. On `suffai-extraction`:

| Name | Value |
|---|---|
| `ENABLE_VERTEX` | `true` |
| `VERTEX_LOCATION` | `asia-south1` |
| `VERTEX_MODEL` | `gemini-2.0-flash` (or approved model) |

3. Grant `suffai-extraction-sa` **Vertex AI User**.
4. Test Capture → upload → Review; without Vertex the worker abstains safely.

---

## Phase D — BigQuery warehouse + ingestion / analytics jobs

Blueprint: Mongo/GCS → Cloud Run ingestion jobs → BigQuery raw/staging/marts → Cloud Scheduler.

### D1. BigQuery datasets

**BigQuery → Create dataset** (location `asia-south1`) or run:

```text
analytics/bigquery/001_datasets.sql
analytics/bigquery/002_star_schema.sql
```

| Dataset | Role |
|---|---|
| `suffai_raw` | Immutable source snapshots / ingest markers |
| `suffai_staging` | Validated facts (`fact_order`, RFM snapshots, …) |
| `suffai_marts` | Privacy-safe aggregates (`mart_geo_trends`, …) |

### D2. Build job image

```bash
gcloud builds submit --config=deploy/cloudbuild-job.yaml
```

Image: `asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-job:latest`

### D3. Cloud Run Job — ingestion (`suffai-cdc`)

```bash
gcloud run jobs create suffai-cdc \
  --image=asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-job:latest \
  --region=asia-south1 \
  --service-account=suffai-ingestion-sa@{PROJECT_ID}.iam.gserviceaccount.com \
  --set-env-vars=GOOGLE_CLOUD_PROJECT={PROJECT_ID},BQ_PROJECT={PROJECT_ID},BQ_LOCATION=asia-south1 \
  --command=python --args=-m,jobs.cdc_ingest
```

Optional: `MONGO_URI` (Secret Manager) for incremental Mongo pull with `updated_at` overlap (Blueprint §6).  
Without Mongo, the job refreshes from `DATA_DIR` / GCS snapshot and still calls BigQuery sync when credentials allow.

### D4. Cloud Run Job — analytics (`suffai-bq-sync`)

```bash
gcloud run jobs create suffai-bq-sync \
  --image=asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-job:latest \
  --region=asia-south1 \
  --service-account=suffai-analytics-sa@{PROJECT_ID}.iam.gserviceaccount.com \
  --set-env-vars=GOOGLE_CLOUD_PROJECT={PROJECT_ID},BQ_PROJECT={PROJECT_ID},USE_BIGQUERY_MARTS=true \
  --command=python --args=-m,jobs.bq_sync
```

Manual run:

```bash
gcloud run jobs execute suffai-bq-sync --region=asia-south1
gcloud run jobs execute suffai-cdc --region=asia-south1
```

### D5. Cloud Scheduler (Blueprint §1 / §2)

Create HTTP jobs that invoke Cloud Run Jobs (see also `deploy/scheduler.yaml` and `infra/terraform/modules/scheduler`):

| Scheduler job | Cron (Asia/Kolkata) | Target |
|---|---|---|
| `suffai-cdc-hourly` | `0 * * * *` | Job `suffai-cdc` `:run` |
| `suffai-bq-marts-daily` | `15 2 * * *` | Job `suffai-bq-sync` `:run` |

Use OAuth via `suffai-scheduler-sa`.  
Optional: wrap both in **Workflows** as Blueprint diagram shows (`SCH → WF → I / SEG`).

### D6. Serve marts from the API

On `suffai-api`:

| Name | Value |
|---|---|
| `USE_BIGQUERY_MARTS` | `true` |
| `BQ_PROJECT` | `{PROJECT_ID}` |
| `BQ_MARTS_DATASET` | `suffai_marts` |

Geo tiles/choropleth then prefer `mart_geo_trends` (fallback to local normalize if BQ fails).

**Gate:** Job succeeds; marts tables populated; `/v1/activation/snapshots/schedule-status` shows CDC watermark after a run.

---

## Phase E — Identity Platform (production AuthN)

Blueprint §2 / §10: AuthN = Identity Platform; AuthZ = tenant/brand in app + Cloud SQL memberships.

1. **Identity Platform** → Enable → Email/Password (or corporate IdP).
2. Create a **Web API key**; restrict HTTP referrers to `{WEB_URL}/*`.
3. Create brand users; optionally set custom claims under `suffai`:

```json
{ "brand": "Peter England", "brand_id": "peterengland", "tenant_id": "tenant-peterengland", "role": "brand_analyst" }
```

4. Or map domains via `IDENTITY_TENANT_MAP_JSON` on the API.
5. Update `suffai-api`:

| Name | Value |
|---|---|
| `IDENTITY_PLATFORM_ENABLED` | `true` |
| `IDENTITY_PLATFORM_PROJECT` | `{PROJECT_ID}` |
| `IDENTITY_WEB_API_KEY` | (browser key) |
| `IDENTITY_TENANT_MAP_JSON` | `{"@yourbrand.com":{"brand":"Peter England","role":"brand_analyst"}}` |
| `DEMO_AUTH_ENABLED` | `false` in prod |

6. Redeploy `suffai-web` if you also set `NEXT_PUBLIC_IDENTITY_API_KEY`.
7. Login UI switches to **Identity Platform** when `/v1/auth/config` reports IP enabled + API key.

**Gate:** Password login via Identity Toolkit → `/v1/auth/identity/session` → Geo loads with tenant claims.

---

## Phase F — Hardening & multi-environment

1. **Projects:** promote `suffai-dev` → `suffai-staging` → `suffai-prod` with separate secrets and SAs (Blueprint §11).
2. **Terraform** (gated):

```bash
cd infra/terraform
terraform init
terraform plan -var="project_id={PROJECT_ID}" -var="enable_resources=false"
# After approval:
terraform apply -var="project_id={PROJECT_ID}" -var="enable_resources=true" \
  -var="scheduler_sa_email=suffai-scheduler-sa@{PROJECT_ID}.iam.gserviceaccount.com"
```

3. Turn off demo auth in prod; require Identity Platform.
4. Lock exports with `EXPORT_REQUIRE_ROLE=brand_analyst`; keep `PRIVACY_DIFFERENCING_ENABLED=true`.
5. Replace demo PIN GeoJSON (`suffai/geo/pin_polygons.geojson`) with **licensed** boundaries before production map claims (Blueprint §2 Maps note).
6. Optional: API Gateway in front of `suffai-api` for OIDC at the edge.
7. Monitoring: alert on Job failures, Pub/Sub DLQ depth, Cloud SQL CPU, API 5xx, ingestion lag (Blueprint §12).

---

## Recommended order (checklist)

```text
[ ] A1–A5  Project, APIs, Artifact Registry, secrets, SAs
[ ] B1–B5  suffai-api → suffai-web → CORS → suffai-extraction → browser smoke
[ ] C1–C2  GCS buckets + Pub/Sub + DLQ
[ ] C3     Cloud SQL suffai-ops + migrations + DATABASE_URL
[ ] C4     Vertex (only when budget approved)
[ ] D1–D6  BigQuery datasets + suffai-cdc + suffai-bq-sync + Scheduler
[ ] E      Identity Platform (disable demo auth in prod)
[ ] F      Terraform enable, staging/prod promotion, privacy/map license
```

---

## Quick commands cheat sheet

```bash
# One-shot demo deploy (api + web + worker)
export GOOGLE_CLOUD_PROJECT={PROJECT_ID}
export GCP_REGION=asia-south1
./deploy/deploy_cloud_run.sh

# Individual Cloud Build targets
gcloud builds submit --config=deploy/cloudbuild-api.yaml
gcloud builds submit --config=deploy/cloudbuild-web.yaml
gcloud builds submit --config=deploy/cloudbuild-worker.yaml
gcloud builds submit --config=deploy/cloudbuild-job.yaml

# Jobs
gcloud run jobs execute suffai-bq-sync --region=asia-south1
gcloud run jobs execute suffai-cdc --region=asia-south1

# Logs
gcloud run services logs read suffai-api --region=asia-south1 --limit=50
```

---

## Demo credentials (Phase B only)

| Field | Value |
|---|---|
| Brand | `Peter England` (any from `/v1/auth/brands`) |
| Password | `brand123` |

Replace with Identity Platform users before production.

---

## Related docs

| Doc | Purpose |
|---|---|
| `docs/GCP_CONSOLE_DEPLOY_AND_TEST.md` | Longer Console click-path + troubleshooting |
| `docs/PRD_COVERAGE.md` | Feature → code map |
| `docs/ASSUMPTIONS.md` | Source-of-truth rules + open sign-offs |
| `docs/ADR-001-architecture.md` | Cloud Run architecture decision |
| `worker/README.md` | Extraction worker env (`DATABASE_URL`, Vertex) |
| `deploy/scheduler.yaml` | Scheduler job names and cron |
| Blueprint (external) | Canonical GCP service mapping and ERD |

---

## Ground rules (do not violate during deploy)

From Blueprint §0 / PRD:

1. `orders.customer_id` → `customers._id` (never `user_id`).
2. Laundry commodity quantities are **not** fashion purchases.
3. No garment brand/color/size without photo/tag evidence + consent.
4. Brand APIs never return names, phones, emails, addresses, or raw image object keys.
5. Privacy k=30; suppress differencing-prone cohorts.
6. Chat must not run unrestricted generated SQL against BigQuery.
