# Deploy Campaign Impact Metrics (1P) on GCP

Operator runbook to ship **demo campaign impact KPIs** (Reach, Impressions, CTR, Conversions, Revenue, Lift, ROAS) to an existing Suffai Command Center on GCP.

**What this change needs**

| Piece | Why |
|---|---|
| Cloud SQL migration `007_campaign_impact_metrics.sql` | Persist synthetic KPIs (`campaign_impact_metrics`) |
| Cloud Run `suffai-command-center` redeploy | Seed on create + JOIN into list/get APIs + Ads Lab / Admin UI |

**Out of scope for this guide:** first-time project bootstrap (use [GCP_DEPLOY_STEP_BY_STEP.md](./GCP_DEPLOY_STEP_BY_STEP.md) / [GCP_COMMAND_CENTER.md](./GCP_COMMAND_CENTER.md)).

Default region below: **`asia-south1`**. Change if yours differs.

---

## Prerequisites

1. GCP project with billing, `gcloud` authenticated.
2. Cloud Run service **`suffai-command-center`** already deployed.
3. Cloud SQL instance used by Command Center (typically `suffai-ops`) with `DATABASE_URL` attached to that service.
4. Brand campaign tables already applied (**`005`** and **`006`**). If not, apply them before `007` (see Step 2).
5. Local clone of this repo with the impact-metrics changes committed/checked out.

```bash
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
export GCP_REGION=asia-south1
gcloud config set project "$GOOGLE_CLOUD_PROJECT"
gcloud config set run/region "$GCP_REGION"
```

Confirm the service exists:

```bash
gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)'
```

---

## Step 1 — Confirm Cloud SQL + `DATABASE_URL` on Command Center

Impact seed/list/get all use PostgreSQL via `DATABASE_URL`. Without it, campaign create will log a warning and lists will fail the JOIN.

```bash
gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='yaml(spec.template.spec.containers[0].env,spec.template.metadata.annotations)'
```

You should see:

- Env **`DATABASE_URL`** (often from Secret Manager), and
- Annotation **`run.googleapis.com/cloudsql-instances`** = `{PROJECT_ID}:{REGION}:suffai-ops` (or your instance name).

If missing: Console → Cloud Run → `suffai-command-center` → **Edit & deploy** → **Connections → Cloud SQL** + set `DATABASE_URL`, e.g.:

```text
postgresql://USER:PASSWORD@/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE
```

---

## Step 2 — Apply SQL migrations (Cloud SQL)

Order matters. `007` references `brand_campaigns`.

| File | Purpose |
|---|---|
| `db/migrations/005_brand_sku_campaigns.sql` | `brand_campaigns`, `brand_skus`, … |
| `db/migrations/006_campaign_1p_lifecycle.sql` | Start/Ignore lifecycle + ops counters |
| `db/migrations/007_campaign_impact_metrics.sql` | Demo marketing KPIs table |

### Option A — Cloud SQL Studio (Console)

1. Open **SQL →** your instance → **Cloud SQL Studio** (or **Query**).
2. Connect as the app DB user.
3. Paste and run each file in order (`005` → `006` → `007`) if not already applied.
4. For impact only on an already-migrated DB, run just `007_campaign_impact_metrics.sql`.

### Option B — `psql` via Cloud SQL Auth Proxy

```bash
# Install proxy if needed, then:
cloud-sql-proxy "${GOOGLE_CLOUD_PROJECT}:${GCP_REGION}:suffai-ops" --port=5432 &

export PGPASSWORD='YOUR_DB_PASSWORD'
psql "host=127.0.0.1 port=5432 user=YOUR_DB_USER dbname=YOUR_DB_NAME" \
  -f db/migrations/005_brand_sku_campaigns.sql
psql "host=127.0.0.1 port=5432 user=YOUR_DB_USER dbname=YOUR_DB_NAME" \
  -f db/migrations/006_campaign_1p_lifecycle.sql
psql "host=127.0.0.1 port=5432 user=YOUR_DB_USER dbname=YOUR_DB_NAME" \
  -f db/migrations/007_campaign_impact_metrics.sql
```

### Verify table

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'campaign_impact_metrics'
ORDER BY ordinal_position;
```

Expect: `campaign_id`, `reach`, `impressions`, `ctr`, `conversions`, `revenue`, `lift`, `roas`, `generated_at`, `source`.

---

## Step 3 — Redeploy `suffai-command-center`

This ships the Python seed/JOIN logic and updated `web/ads_lab.html` / `web/admin.html` (static files are baked into the Command Center image).

### Option A — Local Docker + deploy script (matches repo default)

```bash
# Optional: keep Ads Lab extract wired
export EXTRACTION_URL=$(gcloud run services describe suffai-extraction \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)' 2>/dev/null || true)

chmod +x deploy/deploy_command_center.sh
./deploy/deploy_command_center.sh
```

The script builds `Dockerfile.command_center`, pushes to Artifact Registry `suffai/suffai-command-center:latest`, and deploys Cloud Run.

**Note:** `deploy_command_center.sh` sets a subset of env vars. It does **not** clear Secret Manager / Cloud SQL bindings that already exist on the service, but if you previously set `SIMILARITY_URL` / `DATABASE_URL` only via Console, re-check those after deploy. Prefer updating env without wiping secrets:

```bash
# After deploy, if SIMILARITY_URL was dropped, restore via:
# gcloud run services update suffai-command-center --region=$GCP_REGION \
#   --update-env-vars="SIMILARITY_URL=https://...."
```

Or use the integrate helper (redeploys CC with extraction + similarity URLs):

```bash
chmod +x deploy/04_integrate_command_center.sh
./deploy/04_integrate_command_center.sh
```

### Option B — Cloud Build (no local Docker)

```bash
gcloud builds submit --config=deploy/cloudbuild-command-center.yaml .
```

Then deploy the built image (or use Console **Edit & deploy new revision** pointing at):

```text
asia-south1-docker.pkg.dev/PROJECT_ID/suffai/suffai-command-center:latest
```

```bash
IMG="${GCP_REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/suffai/suffai-command-center:latest"

gcloud run deploy suffai-command-center \
  --image="$IMG" \
  --region="$GCP_REGION" \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --platform=managed
```

Preserve existing env/secrets/Cloud SQL in the Console if you only change the image.

---

## Step 4 — Smoke test

```bash
URL=$(gcloud run services describe suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" \
  --format='value(status.url)')

curl -sS "$URL/health"
echo "UI: $URL/login.html"
```

1. Log in as a **brand** user → Ads Lab → **My campaigns**.
2. Create a campaign (rank SKUs / extract-rank path). Metrics are seeded **on create**, not on Start.
3. Confirm columns: Reach, Impr., CTR, Conv., Rev., Lift, ROAS (tooltips say “Demo metrics”).
4. Log in as **admin** → 1P campaigns table → same columns.
5. API check (with a valid session/token as your ACL expects):

```bash
# After browser login you can inspect Network → GET /v1/ads-lab/campaigns
# Response item should include:
# "impact": { "reach", "impressions", "ctr", "conversions", "revenue", "lift", "roas", "source": "synthetic" }
```

6. Refresh the page — values must **not** change (persisted row; no re-randomize).

### Existing campaigns (created before `007`)

Older rows have **no** `campaign_impact_metrics` row until you either:

- Create a **new** campaign (seed runs automatically), or
- Manually insert demo rows / call `seed_synthetic_impact` from an ops shell (not exposed as an HTTP endpoint).

---

## Step 5 — Logs if something fails

```bash
gcloud run services logs read suffai-command-center \
  --region="$GCP_REGION" --project="$GOOGLE_CLOUD_PROJECT" --limit=50
```

Look for:

| Symptom | Likely cause |
|---|---|
| `seed_synthetic_impact failed` | Migration `007` not applied, or `DATABASE_URL` / Cloud SQL attach missing |
| `relation "campaign_impact_metrics" does not exist` | Run Step 2 |
| List campaigns 500 after deploy | JOIN fails without table — apply `007` then retry |
| Columns show `—` | Campaign predates seed; create a new campaign |

---

## Checklist

- [ ] `DATABASE_URL` + Cloud SQL connection on `suffai-command-center`
- [ ] Migrations `005` → `006` → `007` applied (or `007` alone if 1P already live)
- [ ] Image rebuilt and Cloud Run revision serving
- [ ] Brand My campaigns shows demo KPI columns
- [ ] Admin 1P campaigns shows same columns
- [ ] New campaign gets stable `impact` in API; Start does **not** regenerate metrics

---

## Related docs

- Full stack bootstrap: [GCP_DEPLOY_STEP_BY_STEP.md](./GCP_DEPLOY_STEP_BY_STEP.md)
- Command Center overview: [GCP_COMMAND_CENTER.md](./GCP_COMMAND_CENTER.md)
- Deploy script: `deploy/deploy_command_center.sh`
- Migration: `db/migrations/007_campaign_impact_metrics.sql`
