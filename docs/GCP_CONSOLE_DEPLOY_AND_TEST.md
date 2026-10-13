# Suffai — Deploy & Test on GCP (Console UI)

Step-by-step guide using the **Google Cloud Console** (browser UI).  
Includes exact **file names**, **service names**, and **configuration values** from this repo.

> **Region used in this guide:** `asia-south1` (Mumbai)  
> Change consistently if your residency requirement differs.  
> **Do not enable Vertex** (`ENABLE_VERTEX=false`) until you accept paid API usage.

---

## 0. What you will deploy


| Cloud resource                  | Exact name                                     | Built from                              | Listens on                          |
| ------------------------------- | ---------------------------------------------- | --------------------------------------- | ----------------------------------- |
| Cloud Run service               | `suffai-api`                                   | `Dockerfile.api` (or root `Dockerfile`) | `PORT=8080`                         |
| Cloud Run service               | `suffai-web`                                   | `Dockerfile.web`                        | `PORT=8080`                         |
| Cloud Run service               | `suffai-extraction`                            | `Dockerfile.worker`                     | `PORT=8080`                         |
| Artifact Registry repo          | `suffai`                                       | —                                       | Docker format, region `asia-south1` |
| Secret Manager secret           | `suffai-jwt-secret`                            | —                                       | Used as `JWT_SECRET`                |
| GCS bucket (optional)           | `{PROJECT_ID}-suffai-raw`                      | fixtures under `data_offline/`          | —                                   |
| GCS bucket (optional)           | `{PROJECT_ID}-suffai-garment-images`           | private images                          | —                                   |
| BigQuery datasets (optional)    | `suffai_raw`, `suffai_staging`, `suffai_marts` | DDL in `analytics/bigquery/`            | location `asia-south1`              |
| Pub/Sub topic (optional)        | `garment-image-uploaded`                       | —                                       | worker push later                   |
| Pub/Sub DLQ topic (optional)    | `garment-image-uploaded-dlq`                   | —                                       | —                                   |
| Pub/Sub subscription (optional) | `garment-image-uploaded-push`                  | —                                       | push → worker                       |


**Source layout (important files):**


| Path                                | Role                                                  |
| ----------------------------------- | ----------------------------------------------------- |
| `Dockerfile.api`                    | API image                                             |
| `Dockerfile.web`                    | Next.js web image                                     |
| `Dockerfile.worker`                 | Extraction worker image                               |
| `Dockerfile`                        | Default = API (same as `Dockerfile.api`)              |
| `cloudbuild.yaml`                   | Multi-service build + deploy                          |
| `api/main.py`                       | FastAPI entrypoint `api.main:app`                     |
| `worker/main.py`                    | Worker entrypoint `worker.main:app`                   |
| `web/`                              | Next.js App Router UI                                 |
| `data_offline/`                     | Read-only JSON fixtures baked into API image          |
| `data_derived/`                     | Local/derived garment JSON (empty until IMG pipeline) |
| `db/migrations/001_operational.sql` | Cloud SQL schema (Phase later)                        |
| `contracts/openapi.yaml`            | API contract                                          |
| `deploy/env.example`                | Env var reference                                     |
| `config/settings.py`                | Runtime env mapping                                   |


**Demo login (web UI):**


| Field    | Value                                                 |
| -------- | ----------------------------------------------------- |
| Brand    | `Peter England` (or any brand from `/v1/auth/brands`) |
| Password | `brand123`                                            |


---



## 1. Create / select a GCP project

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Top bar → **Select a project** → **New project**.
3. Suggested name: `suffai-dev` (or `suffai-staging` / `suffai-prod`).
4. Note the **Project ID** (you will substitute `{PROJECT_ID}` below).
5. Ensure **Billing** is linked: **Billing** → link a billing account.

---



## 2. Enable required APIs

Go to **APIs & Services** → **Library** and enable:


| API                                          | Why                                                  |
| -------------------------------------------- | ---------------------------------------------------- |
| **Cloud Run API**                            | Host `suffai-api`, `suffai-web`, `suffai-extraction` |
| **Artifact Registry API**                    | Store Docker images (`suffai` repo)                  |
| **Cloud Build API**                          | Build from `Dockerfile.`* / `cloudbuild.yaml`        |
| **Secret Manager API**                       | Store `JWT_SECRET`                                   |
| **Cloud Logging API**                        | Runtime logs                                         |
| **Cloud Storage API**                        | Optional fixture / image buckets                     |
| **BigQuery API**                             | Optional analytics datasets                          |
| **Pub/Sub API**                              | Optional garment upload events                       |
| **Cloud SQL Admin API**                      | Optional later (PostgreSQL)                          |
| **Identity and Access Management (IAM) API** | Service accounts / permissions                       |


Wait until all show **Enabled**.

---



## 3. Set the default region

1. Open **Cloud Run**.
2. Prefer region `asia-south1` for every Cloud Run service in this guide.
3. Keep the same region for Artifact Registry and (if used) Cloud SQL / BQ location.

---



## 4. Create Artifact Registry repository

1. Open **Artifact Registry** → **Repositories** → **Create repository**.
2. Configure:


| Field         | Value         |
| ------------- | ------------- |
| Name          | `suffai`      |
| Format        | **Docker**    |
| Mode          | Standard      |
| Location type | Region        |
| Region        | `asia-south1` |


1. Click **Create**.

Images will be:

```text
asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-api:latest
asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-web:latest
asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-extraction:latest
```

---



## 5. Put the source code where Cloud Build can see it

Pick one:

### Option A — GitHub / GitLab / Bitbucket (recommended)

1. Push this repo (`AI_CUP`) to a remote.
2. In Console: **Cloud Build** → **Repositories** → **Connect repository**.
3. Authorize and select the repo/branch (e.g. `main`).



### Option B — Cloud Source Repositories

1. **Source Repositories** → **Create repository** → name `suffai`.
2. Push or upload the project files (must include `Dockerfile.api`, `Dockerfile.web`, `Dockerfile.worker`, `cloudbuild.yaml`, `api/`, `web/`, `worker/`, `suffai/`, `data_offline/`).



### Option C — Cloud Shell upload (fastest for a one-off)

1. Open **Activate Cloud Shell** (terminal icon).
2. Upload a zip of the project or `git clone` your remote.
3. `cd` into the project root (folder that contains `Dockerfile.api`).

---



## 6. Create the JWT secret

1. Open **Secret Manager** → **Create secret**.
2. Configure:


| Field        | Value                                      |
| ------------ | ------------------------------------------ |
| Name         | `suffai-jwt-secret`                        |
| Secret value | A long random string (**≥ 32 characters**) |


1. Click **Create secret**.

You will mount this into Cloud Run as env var `JWT_SECRET`.

---



## 7. Build and deploy with Cloud Build (Console)

This is the simplest UI path that builds all three images using repo file `cloudbuild.yaml`.

### 7.1 First build: API only path (manual, reliable)

If you want to deploy services one-by-one from the UI:

#### 7.1.1 Build `suffai-api` image (Cloud Shell)

In Cloud Shell, from the project root:

```bash
# From repo root (folder that contains Dockerfile.api)
gcloud builds submit --config=deploy/cloudbuild-api.yaml .
```

If it fails, fetch the real Docker error (the "status: 1" line alone is not enough):

```bash
gcloud builds list --limit=1 --format='value(id)'
gcloud builds log "$(gcloud builds list --limit=1 --format='value(id)')" | tail -80
```

> Prefer `deploy/cloudbuild-*.yaml` over ad-hoc `/dev/stdin` configs — Cloud Shell heredocs often break variable expansion.



#### 7.1.2 Deploy `suffai-api` in Cloud Run UI

1. Open **Cloud Run** → **Create service**.
2. Choose **Deploy one revision from an existing container image**.
3. Select image:
  `asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-api:latest`
4. Configure:


| Field           | Value                                                        |
| --------------- | ------------------------------------------------------------ |
| Service name    | `suffai-api`                                                 |
| Region          | `asia-south1`                                                |
| Authentication  | **Allow public access** (demo) — tighten later               |
| Ingress         | All                                                          |
| Container port  | `8080`                                                       |
| CPU allocation  | CPU is only allocated during request processing (default OK) |
| Memory          | `1 GiB`                                                      |
| CPU             | `1`                                                          |
| Request timeout | `300` seconds                                                |
| Max instances   | `5` (demo)                                                   |
| Min instances   | `0`                                                          |


1. **Variables & secrets** → **Add variable**:


| Name                   | Value               |
| ---------------------- | ------------------- |
| `PORT`                 | `8080`              |
| `ENVIRONMENT`          | `staging`           |
| `DEMO_AUTH_ENABLED`    | `true`              |
| `DATA_DIR`             | `/app/data_offline` |
| `DERIVED_DIR`          | `/app/data_derived` |
| `GOOGLE_CLOUD_PROJECT` | `{PROJECT_ID}`      |
| `GCP_REGION`           | `asia-south1`       |
| `BQ_PROJECT`           | `{PROJECT_ID}`      |
| `ENABLE_VERTEX`        | `false`             |
| `PRIVACY_K`            | `30`                |
| `JWT_ISSUER`           | `suffai-api`        |


1. **Reference a secret** for JWT:


| Name         | Secret              | Version  |
| ------------ | ------------------- | -------- |
| `JWT_SECRET` | `suffai-jwt-secret` | `latest` |


Grant the Cloud Run runtime service account **Secret Manager Secret Accessor** on `suffai-jwt-secret` if prompted.

1. Click **Create**.
2. Copy the service URL, e.g.:

```text
https://suffai-api-XXXXXXXX-as.a.run.app
```

Call this `API_URL` for the rest of the guide.

### 7.2 Deploy `suffai-web`



#### 7.2.1 Build web image (must bake `API_URL`)

In Cloud Shell:

```bash
# Paste your real API URL
export API_URL="https://suffai-api-XXXXXXXX-as.a.run.app"

gcloud builds submit \
  --config=deploy/cloudbuild-web.yaml \
  --substitutions=_API_URL=${API_URL} \
  .
```

#### 7.2.2 Create Cloud Run service `suffai-web`

1. **Cloud Run** → **Create service**.
2. Image:
  `asia-south1-docker.pkg.dev/{PROJECT_ID}/suffai/suffai-web:latest`
3. Configure:


| Field          | Value                      |
| -------------- | -------------------------- |
| Service name   | `suffai-web`               |
| Region         | `asia-south1`              |
| Authentication | Allow public access (demo) |
| Container port | `8080`                     |
| Memory         | `512 MiB` (or `1 GiB`)     |
| CPU            | `1`                        |


1. Environment variables:


| Name       | Value        |
| ---------- | ------------ |
| `PORT`     | `8080`       |
| `NODE_ENV` | `production` |


> Note: `NEXT_PUBLIC_API_URL` is a **build-time** arg in `Dockerfile.web`, not a runtime env. Rebuild the image if `API_URL` changes.

1. Click **Create**.
2. Copy `WEB_URL`, e.g. `https://suffai-web-XXXXXXXX-as.a.run.app`.



### 7.3 Point API CORS at the web URL

1. Open Cloud Run service `suffai-api` → **Edit & deploy new revision**.
2. Update / add variables:


| Name             | Value                                      |
| ---------------- | ------------------------------------------ |
| `WEB_ORIGIN`     | `{WEB_URL}` (exact URL, no trailing slash) |
| `API_PUBLIC_URL` | `{API_URL}`                                |


Example:

```text
WEB_ORIGIN=https://suffai-web-XXXXXXXX-as.a.run.app
API_PUBLIC_URL=https://suffai-api-XXXXXXXX-as.a.run.app
```

1. Deploy.



### 7.4 Deploy worker `suffai-extraction`



#### Build

```bash
# From repo root
gcloud builds submit --config=deploy/cloudbuild-worker.yaml .
```

On failure:

```bash
gcloud builds log "$(gcloud builds list --limit=1 --format='value(id)')" | tail -80
```



#### Create service


| Field          | Value                                   |
| -------------- | --------------------------------------- |
| Service name   | `suffai-extraction`                     |
| Region         | `asia-south1`                           |
| Authentication | **Require authentication** (not public) |
| Container port | `8080`                                  |
| Memory         | `512 MiB`                               |


Environment variables:


| Name              | Value              |
| ----------------- | ------------------ |
| `PORT`            | `8080`             |
| `ENABLE_VERTEX`   | `false`            |
| `VERTEX_LOCATION` | `asia-south1`      |
| `VERTEX_MODEL`    | `gemini-2.0-flash` |




### 7.5 Optional: one-shot via `cloudbuild.yaml` (Console Trigger)

1. **Cloud Build** → **Triggers** → **Create trigger**.
2. Event: Push to branch `main` (or manual).
3. Configuration: **Cloud Build configuration file**.
4. Location: `cloudbuild.yaml` (repo root).
5. Substitution variables:


| Variable          | Example                                      |
| ----------------- | -------------------------------------------- |
| `_REGION`         | `asia-south1`                                |
| `_API_SERVICE`    | `suffai-api`                                 |
| `_WEB_SERVICE`    | `suffai-web`                                 |
| `_WORKER_SERVICE` | `suffai-extraction`                          |
| `_API_URL`        | your real `API_URL` (after first API deploy) |


1. Run the trigger twice if needed: first for API, then again after `_API_URL` is known for web.

Also grant the **Cloud Build service account** roles:

- `Cloud Run Admin`
- `Service Account User`
- `Artifact Registry Writer`
- `Secret Manager Secret Accessor` (if used in builds)

---



## 8. Optional supporting resources (Console)

These are not required for the first API + Web smoke test (fixtures are inside the API image under `/app/data_offline`).

### 8.1 Cloud Storage buckets

**Cloud Storage** → **Create**:


| Bucket name                          | Access           | Purpose        |
| ------------------------------------ | ---------------- | -------------- |
| `{PROJECT_ID}-suffai-raw`            | Uniform, private | Raw snapshots  |
| `{PROJECT_ID}-suffai-garment-images` | Uniform, private | Garment photos |


If you sync fixtures from GCS later, set on `suffai-api`:


| Name               | Value                                |
| ------------------ | ------------------------------------ |
| `GCS_BUCKET`       | `{PROJECT_ID}-suffai-raw`            |
| `GCS_RAW_BUCKET`   | `{PROJECT_ID}-suffai-raw`            |
| `GCS_IMAGE_BUCKET` | `{PROJECT_ID}-suffai-garment-images` |
| `GCS_DATA_PREFIX`  | `data_offline/`                      |


Upload JSON files from local folder `data_offline/` into `gs://{PROJECT_ID}-suffai-raw/data_offline/`.

### 8.2 BigQuery datasets

**BigQuery** → **Create dataset** (three times):


| Dataset ID       | Location      |
| ---------------- | ------------- |
| `suffai_raw`     | `asia-south1` |
| `suffai_staging` | `asia-south1` |
| `suffai_marts`   | `asia-south1` |


DDL reference files:

- `analytics/bigquery/001_datasets.sql`
- `analytics/bigquery/002_star_schema.sql`

Set on API: `BQ_PROJECT={PROJECT_ID}`, `BQ_LOCATION=asia-south1`.

### 8.3 Pub/Sub (for extraction later)

**Pub/Sub** → create topics:

- `garment-image-uploaded`
- `garment-image-uploaded-dlq`

Create subscription `garment-image-uploaded-push`:


| Field                 | Value                                                                         |
| --------------------- | ----------------------------------------------------------------------------- |
| Delivery type         | Push                                                                          |
| Endpoint URL          | `https://suffai-extraction-XXXXXXXX-as.a.run.app/`                            |
| Auth                  | Enable OIDC → service account with `roles/run.invoker` on `suffai-extraction` |
| Dead letter topic     | `garment-image-uploaded-dlq`                                                  |
| Max delivery attempts | `5`                                                                           |


Env already expected by code:

- `PUBSUB_IMAGE_TOPIC=garment-image-uploaded`
- `PUBSUB_IMAGE_SUBSCRIPTION=garment-image-uploaded-push`



### 8.4 Cloud SQL (Phase later)

When ready:

1. **SQL** → **Create instance** → PostgreSQL.
2. Suggested instance ID: `suffai-ops`.
3. Region: `asia-south1`.
4. Apply schema from `db/migrations/001_operational.sql` (Cloud SQL Studio or Cloud Shell `psql`).
5. Connect Cloud Run `suffai-api` with Cloud SQL connection and set `DATABASE_URL` / `CLOUD_SQL_INSTANCE={PROJECT_ID}:asia-south1:suffai-ops`.

Not required for the fixture-based geo/RFM demo.

---



## 9. IAM checklist (minimum)


| Principal                             | Role                           | Resource            |
| ------------------------------------- | ------------------------------ | ------------------- |
| Cloud Run runtime SA for `suffai-api` | Secret Manager Secret Accessor | `suffai-jwt-secret` |
| Cloud Build SA                        | Artifact Registry Writer       | repo `suffai`       |
| Cloud Build SA                        | Cloud Run Admin                | project             |
| Cloud Build SA                        | Service Account User           | project             |
| Pub/Sub push SA (later)               | Cloud Run Invoker              | `suffai-extraction` |
| `suffai-api` SA (if using GCS)        | Storage Object Viewer          | raw bucket          |


Find runtime SA on each Cloud Run service → **Security** tab.

---



## 10. Test in the browser / Console



### 10.1 API health (browser)

Open:

```text
{API_URL}/health
```

Expect JSON roughly:

```json
{
  "status": "ok",
  "service": "suffai-api",
  "environment": "staging",
  "data_dir": "/app/data_offline"
}
```

Also open:

```text
{API_URL}/ready
{API_URL}/docs
```

`/docs` is the interactive OpenAPI UI (Swagger). Contract file in repo: `contracts/openapi.yaml`.

### 10.2 Integrity report

In `/docs` or browser:

```text
{API_URL}/v1/integrity/report
```

Confirm gates:

- `correct_customer_join_majority: true`
- `wrong_user_id_join_near_zero: true`
- `lines_exist: true`



### 10.3 Auth + segments + geo (Swagger UI)

1. Open `{API_URL}/docs`.
2. **POST** `/v1/auth/brand/login` body:

```json
{
  "brand": "Peter England",
  "password": "brand123"
}
```

1. Copy `data.access_token`.
2. Click **Authorize** in Swagger → `Bearer <token>`.
3. **GET** `/v1/segments/summary` → should return RFM distribution (`customers_scored` ~ thousands).
4. **POST** `/v1/geo/query` body:

```json
{
  "geo_level": "pin",
  "geo_ids": ["560103"],
  "metrics": ["active_customers", "commodity_popularity"]
}
```

Expect `tiles`, `commodities`, `rfm_mix`, and `meta.coverage_note`.

1. **GET** `/v1/brands/peterengland/benchmarks`
  Expect error code `INSUFFICIENT_DATA` until verified garment photos exist (correct PRD behavior).
2. **POST** `/v1/chat/query` body:

```json
{
  "query": "Which commodities are popular in PIN 560103?"
}
```

Expect `tool: "commodity_popularity"`.

Refuse test:

```json
{ "query": "What is our fashion market share?" }
```

Expect `refused: true`.

### 10.4 Web UI end-to-end

1. Open `{WEB_URL}` → redirects to `/login`.
2. Brand: **Peter England**, password: **brand123**.
3. You should land on **Geo Intelligence** (`/geo`) with charts.
4. Navigate left nav (from `web/src/components/AppShell.tsx`):


| Menu label          | Route        | Expected                               |
| ------------------- | ------------ | -------------------------------------- |
| Geo Intelligence    | `/geo`       | Metrics + charts from `/v1/geo/query`  |
| Brand & Competition | `/brand`     | Empty / insufficient verified garments |
| Product Research    | `/research`  | Empty-state messaging                  |
| Campaign Lab        | `/campaigns` | Scaffold messaging                     |
| Ask Suffai          | `/chat`      | Answers via `/v1/chat/query`           |


1. If login fails with CORS errors: re-check `WEB_ORIGIN` on `suffai-api` matches `{WEB_URL}` exactly.



### 10.5 Worker health

From Cloud Shell (authenticated invoke):

```bash
WORKER_URL=$(gcloud run services describe suffai-extraction --region=asia-south1 --format='value(status.url)')
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" ${WORKER_URL}/health
```

Expect:

```json
{"status":"ok","service":"suffai-extraction","vertex_enabled":false}
```



### 10.6 Logs (Console)

1. **Cloud Run** → select service → **Logs**.
2. Or **Logs Explorer** filter:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="suffai-api"
```

Repeat for `suffai-web` and `suffai-extraction`.

---



## 11. Configuration cheat sheet (copy/paste)



### `suffai-api` env

```text
PORT=8080
ENVIRONMENT=staging
DEMO_AUTH_ENABLED=true
DATA_DIR=/app/data_offline
DERIVED_DIR=/app/data_derived
GOOGLE_CLOUD_PROJECT={PROJECT_ID}
GCP_REGION=asia-south1
BQ_PROJECT={PROJECT_ID}
ENABLE_VERTEX=false
PRIVACY_K=30
JWT_ISSUER=suffai-api
JWT_SECRET=*** from Secret Manager: suffai-jwt-secret ***
WEB_ORIGIN={WEB_URL}
API_PUBLIC_URL={API_URL}
```



### `suffai-web` env

```text
PORT=8080
NODE_ENV=production
```

Build-arg (in `Dockerfile.web`):

```text
NEXT_PUBLIC_API_URL={API_URL}
```



### `suffai-extraction` env

```text
PORT=8080
ENABLE_VERTEX=false
VERTEX_LOCATION=asia-south1
VERTEX_MODEL=gemini-2.0-flash
```



### Dockerfiles ↔ services


| Dockerfile          | Cloud Run service   | Container command                                       |
| ------------------- | ------------------- | ------------------------------------------------------- |
| `Dockerfile.api`    | `suffai-api`        | `uvicorn api.main:app --host 0.0.0.0 --port ${PORT}`    |
| `Dockerfile.web`    | `suffai-web`        | `node server.js` (Next standalone)                      |
| `Dockerfile.worker` | `suffai-extraction` | `uvicorn worker.main:app --host 0.0.0.0 --port ${PORT}` |


---



## 12. Smoke-test checklist

- [ ] APIs enabled (Cloud Run, Artifact Registry, Cloud Build, Secret Manager)
- [ ] Artifact Registry repo `suffai` in `asia-south1`
- [ ] Secret `suffai-jwt-secret` created
- [ ] `suffai-api` healthy at `/health` and `/docs`
- [ ] `/v1/integrity/report` gates pass
- [ ] Login returns JWT (`/v1/auth/brand/login`)
- [ ] Geo query returns tiles for PIN `560103`
- [ ] Brand benchmarks return `INSUFFICIENT_DATA` (expected)
- [ ] Chat answers commodity question; refuses market share
- [ ] `suffai-web` login works with `brand123`
- [ ] Geo page renders charts
- [ ] `WEB_ORIGIN` set so browser calls to API are not blocked
- [ ] `suffai-extraction` `/health` OK with auth
- [ ] `ENABLE_VERTEX` remains `false` until intentionally enabled

---



## 13. Troubleshooting


| Symptom                     | Fix                                                                              |
| --------------------------- | -------------------------------------------------------------------------------- |
| Web login “API unreachable” | Rebuild `suffai-web` with correct `NEXT_PUBLIC_API_URL={API_URL}`                |
| Browser CORS error          | Set `WEB_ORIGIN` on `suffai-api` to exact `{WEB_URL}`                            |
| `/ready` false              | Ensure image includes `data_offline/` (see `Dockerfile.api` `COPY data_offline`) |
| 401 on brand routes         | Use Bearer token from `/v1/auth/brand/login`                                     |
| Secret mount fails          | Grant runtime SA `roles/secretmanager.secretAccessor`                            |
| Cloud Build can’t push      | Enable Artifact Registry API; grant Cloud Build SA writer on repo `suffai`       |
| Worker 403 from Pub/Sub     | Grant push SA `roles/run.invoker` on `suffai-extraction`                         |
| Out of memory on API        | Raise Cloud Run memory to `1–2 GiB` (RFM over ~20k orders)                       |
| `build step … status: 1`    | Only an exit code — run `gcloud builds log <BUILD_ID> \| tail -80` for the real error |
| Worker pip failures         | Use `Dockerfile.worker` + `requirements-worker.txt` via `deploy/cloudbuild-worker.yaml` |
| Web `COPY … /app/public` fails | Re-sync latest `Dockerfile.web` (it `mkdir -p /app/public` in the builder). Rebuild with `deploy/cloudbuild-web.yaml` |
| Heredoc `/dev/stdin` fails  | Prefer `deploy/cloudbuild-*.yaml` configs                                       |


---



## 14. What not to do yet

- Do **not** set `ENABLE_VERTEX=true` without budget approval.
- Do **not** apply Terraform with `enable_resources=true` until project/region/billing are confirmed (`infra/terraform/main.tf`).
- Do **not** deploy anything under `archive/streamlit/` — deprecated.
- Do **not** expose raw customer PII on brand APIs (already blocked by design).

---



## 15. Quick reference — Console click path

```text
Console
 ├─ APIs & Services → enable APIs
 ├─ Artifact Registry → create repo "suffai"
 ├─ Secret Manager → create "suffai-jwt-secret"
 ├─ Cloud Build → submit Dockerfile.api / Dockerfile.web / Dockerfile.worker
 ├─ Cloud Run → create "suffai-api" → copy API_URL
 ├─ Cloud Run → create "suffai-web" (built with NEXT_PUBLIC_API_URL) → copy WEB_URL
 ├─ Cloud Run → edit "suffai-api" → set WEB_ORIGIN + API_PUBLIC_URL
 ├─ Cloud Run → create "suffai-extraction" (auth required)
 ├─ (Optional) Cloud Storage / BigQuery / Pub/Sub
 └─ Test: API_URL/docs + WEB_URL/login
```

When this checklist is green, the GCP demo path for Geo / RFM / Ask Suffai is live; Brand/Campaign unlock after the garment image pipeline.

---

## 16. Production hardening (Identity / BigQuery / CDC / ADV)

### 16.1 Identity Platform

1. **Identity Platform** → enable Email/Password (or federated IdP).
2. Create a Web API key; restrict by HTTP referrer to `WEB_URL/*`.
3. On `suffai-api` set:
   - `IDENTITY_PLATFORM_ENABLED=true`
   - `IDENTITY_PLATFORM_PROJECT={PROJECT_ID}`
   - `IDENTITY_WEB_API_KEY={browser key}`
   - `IDENTITY_TENANT_MAP_JSON` mapping emails/domains → `{brand, role, tenant_id}`
   - Prefer `DEMO_AUTH_ENABLED=false` in prod
4. Optional custom claims under `suffai` on Firebase users: `{brand, brand_id, tenant_id, role}`.
5. Web login auto-switches to Identity when config reports IP enabled + API key.

### 16.2 BigQuery + CDC Cloud Run Jobs

```bash
gcloud builds submit --config=deploy/cloudbuild-job.yaml

gcloud run jobs create suffai-bq-sync \
  --image=asia-south1-docker.pkg.dev/$PROJECT_ID/suffai/suffai-job:latest \
  --region=asia-south1 \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=$PROJECT_ID,USE_BIGQUERY_MARTS=true \
  --command=python --args=-m,jobs.bq_sync

gcloud run jobs create suffai-cdc \
  --image=asia-south1-docker.pkg.dev/$PROJECT_ID/suffai/suffai-job:latest \
  --region=asia-south1 \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=$PROJECT_ID,MONGO_URI=... \
  --command=python --args=-m,jobs.cdc_ingest
```

Wire schedules from `deploy/scheduler.yaml` (or Terraform `modules/scheduler`). On `suffai-api` set `USE_BIGQUERY_MARTS=true` to serve geo tiles from `mart_geo_trends`.

### 16.3 Cloud SQL dual-write

Apply `db/migrations/001_operational.sql` then `002_adv_privacy_ops.sql`. Attach Cloud SQL to `suffai-api` / `suffai-extraction` and set `DATABASE_URL`. API bootstraps ADV/ops tables on startup; `GarmentStore` mirrors JSON docs into `ops_json_documents`.

### 16.4 ADV-01 + privacy

- UI: `/activation` (conversions, observational uplift, media webhook, inventory).
- Env: `ENABLE_ADV`, `MEDIA_ACTIVATION_WEBHOOK`, `PRIVACY_DIFFERENCING_ENABLED`, `EXPORT_REQUIRE_ROLE`.
- Geo choropleth: `GET /v1/geo/choropleth` + PIN GeoJSON under `suffai/geo/pin_polygons.geojson` (replace with licensed boundaries before production map claims).
