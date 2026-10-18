# Redeploy guide — 18 Oct 2026 changes

This guide ships the changes made in the latest working session. Every service involved already exists on GCP. For first-time setup, see [GCP_REDEPLOY_E2E.md](./GCP_REDEPLOY_E2E.md).

Run everything from:

```text
/Users/rahul.kumar/Downloads/AI_CUP/AI_CUP_latest
```

---

## What changed and what it needs

| Change | Files | What you deploy |
|--------|-------|-----------------|
| Campaigns page: a "Create & start campaign" bar, the table renamed to "Your 1P campaigns", and a Campaign insights section labelled Simulated | `web/campaigns.html` | Command Center (**required**) |
| Geo tab: the Logistics & warehouse location is a dropdown, and Audience filters default to Select all | `web/geo.html` | Command Center (**required**) |
| Raw errors are hidden from users: they get a generic message plus a reference number, and the full error is logged | `command_center/main.py`, `chatbot.py`, `admin_intel.py` | Command Center (**required**) |
| Similarity deploy script keeps existing env vars (`--update-env-vars`) | `deploy/deploy_similarity.sh` | Nothing now. It takes effect the next time you deploy similarity |
| Opt-in Cloud Tasks for campaign jobs | `deploy/setup_cloud_tasks.sh`, `deploy/env.example` | Optional script (Step 4) |
| CSV loads into `suffai_ops` use the declared column types instead of guessing | `command_center/scripts/load_csv_bq.py`, `load_to_bq.sh`, `aggregate_metrics.py` | Optional data reload (Step 5) |
| The warehouse job writes the columns declared in the DDL, and three new tables have DDL | `jobs/bq_sync.py`, `analytics/bigquery/002_star_schema.sql` | Optional job image and DDL (Step 6) |

None of these changes needs a Postgres migration or a code change in extraction or similarity.

**Minimum release:** run Steps 0–3 and Step 7. Steps 4–6 are independent, so you can run any of them, later or never.

---

## Step 0 — Local checks

```bash
cd /Users/rahul.kumar/Downloads/AI_CUP/AI_CUP_latest
python3 -m pytest command_center/tests tests/test_bq_sync_schema.py -q    # expect 205 passed
bash -n deploy/deploy_similarity.sh deploy/setup_cloud_tasks.sh command_center/scripts/load_to_bq.sh
```

## Step 1 — Environment and snapshot

```bash
gcloud auth login
gcloud auth application-default login          # needed by Steps 5 and 6 (Python BigQuery client)
gcloud auth configure-docker asia-south1-docker.pkg.dev

export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID     # replace
export GCP_REGION=asia-south1
gcloud config set project "$GOOGLE_CLOUD_PROJECT"
gcloud config set run/region "$GCP_REGION"

export EXTRACTION_URL=$(gcloud run services describe suffai-extraction --format='value(status.url)')
export SIMILARITY_URL=$(gcloud run services describe suffai-similarity --format='value(status.url)')
export CC_URL=$(gcloud run services describe suffai-command-center --format='value(status.url)')

# Save the current env and revision so you can roll back
gcloud run services describe suffai-command-center \
  --format='yaml(spec.template.spec.containers[0].env)' > /tmp/cc-env-before.yaml
gcloud run revisions list --service=suffai-command-center --limit=3 \
  --format='table(metadata.name,status.conditions[0].status)'
```

Write down the newest revision name. Step 8 uses it if you need to roll back.

## Step 2 — Redeploy Command Center (required)

This step ships the campaigns page, the Geo tab and the error masking. The script uses `--update-env-vars`, so `DATABASE_URL`, the Cloud SQL attachment and any Cloud Tasks settings are kept.

```bash
chmod +x deploy/deploy_command_center.sh
./deploy/deploy_command_center.sh
```

If Docker isn't running locally, build with Cloud Build and deploy the image instead:

```bash
gcloud builds submit --config=deploy/cloudbuild-command-center.yaml .
gcloud run deploy suffai-command-center \
  --image="${GCP_REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/suffai/suffai-command-center:latest" \
  --region="$GCP_REGION" --allow-unauthenticated --memory=1Gi
```

## Step 3 — Similarity (no redeploy needed)

No code in `similarity/` changed. The script fix only matters the next time you run `./deploy/deploy_similarity.sh`. From then on, a redeploy no longer wipes `DATABASE_URL`.

Before that next redeploy, confirm `DATABASE_URL` is still set. An older deploy with `--set-env-vars` may already have removed it:

```bash
gcloud run services describe suffai-similarity \
  --format='yaml(spec.template.spec.containers[0].env)' | grep -A1 DATABASE_URL
```

If it's missing, restore it with `gcloud run services update suffai-similarity --update-env-vars=DATABASE_URL=...`, or map it from Secret Manager in the Console.

## Step 4 — Cloud Tasks for campaign jobs (optional)

If you skip this step, campaign jobs keep running in a thread inside Command Center, as they do today. If you run it, they move onto a Cloud Tasks queue with retries.

```bash
chmod +x deploy/setup_cloud_tasks.sh
./deploy/setup_cloud_tasks.sh
```

The script is safe to re-run. It:
1. Enables the Cloud Tasks API and creates the `campaign-jobs` queue.
2. Creates the `cc-tasks` service account.
3. Grants Command Center's runtime account permission to queue tasks and to act as `cc-tasks`.
4. Sets `COMMAND_CENTER_URL`, `CLOUD_TASKS_QUEUE` and `TASKS_SERVICE_ACCOUNT` on the service.

Check:

```bash
gcloud tasks queues describe campaign-jobs --location="$GCP_REGION"
gcloud run services describe suffai-command-center \
  --format='yaml(spec.template.spec.containers[0].env)' | grep -E 'CLOUD_TASKS_QUEUE|TASKS_SERVICE_ACCOUNT|COMMAND_CENTER_URL'
```

Start a campaign in the UI. It should reach a finished state, and the logs should show `POST /internal/campaign-jobs/<id>/run`.

To go back to thread mode:

```bash
gcloud run services update suffai-command-center --remove-env-vars=CLOUD_TASKS_QUEUE
```

## Step 5 — Reload the `suffai_ops` CSV tables (optional)

Only run this step if you want `customers`, `orders` and `commodities` reloaded with the declared column types. One visible effect: phone numbers stay text, and `commodities.confidence_score` exists as an empty column instead of being missing.

The reload **replaces** the three tables (`WRITE_TRUNCATE`).

```bash
pip install -r command_center/scripts/requirements.txt     # google-cloud-bigquery
chmod +x command_center/scripts/load_to_bq.sh
./command_center/scripts/load_to_bq.sh "$GOOGLE_CLOUD_PROJECT"
```

If a CSV has a column the DDL doesn't declare, the load stops with that column's name. Add the column to `command_center/scripts/create_bq_tables.sql`, or remove it from the CSV.

Optionally, refresh the chat summary baked into the image, then redo Step 2:

```bash
python3 command_center/scripts/aggregate_metrics.py --bq --project "$GOOGLE_CLOUD_PROJECT"
```

## Step 6 — Warehouse job `suffai-bq-sync` (optional)

Run this step only if you use the star schema (`suffai_staging`, `suffai_marts`, `suffai_raw`), for example with `USE_BIGQUERY_MARTS=true`. Command Center reads `suffai_ops` and is not affected either way.

**6a. Check for tables created by the old job.** The old job let BigQuery guess the columns and created tables without partitions. The new job loads with a fixed schema and day partitions. BigQuery refuses to add partitioning to an existing table, so check first:

```bash
for T in suffai_staging.fact_order suffai_staging.fact_order_commodity \
         suffai_staging.fact_customer_segment_snapshot suffai_marts.mart_geo_trends \
         suffai_marts.mart_commodity_popularity suffai_marts.mart_rfm_distribution; do
  printf '%-48s ' "$T"
  bq show --format=prettyjson "${GOOGLE_CLOUD_PROJECT}:${T}" 2>/dev/null \
    | python3 -c 'import json,sys; t=json.load(sys.stdin); print((t.get("timePartitioning") or {}).get("field","NOT PARTITIONED"))' \
    || echo "absent"
done
```

A table that reports `NOT PARTITIONED` came from the old job. The job rebuilds these tables in full on every run, so dropping them is safe:

```bash
# Only for tables reported as NOT PARTITIONED
bq rm -f -t "${GOOGLE_CLOUD_PROJECT}:suffai_staging.fact_order"
```

Leave `suffai_raw.ingest_runs` alone. It isn't partitioned by design, and it keeps the run history.

**6b. Apply the DDL.** These statements create only missing tables (`IF NOT EXISTS`):

```bash
bq query --location="$GCP_REGION" --use_legacy_sql=false < analytics/bigquery/001_datasets.sql
bq query --location="$GCP_REGION" --use_legacy_sql=false < analytics/bigquery/002_star_schema.sql
```

**6c. Build the job image and update or create the job:**

```bash
gcloud builds submit --config=deploy/cloudbuild-job.yaml .
JOB_IMG="${GCP_REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/suffai/suffai-job:latest"

if gcloud run jobs describe suffai-bq-sync --region="$GCP_REGION" >/dev/null 2>&1; then
  gcloud run jobs update suffai-bq-sync --image="$JOB_IMG" --region="$GCP_REGION"
else
  gcloud run jobs create suffai-bq-sync --image="$JOB_IMG" --region="$GCP_REGION" \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},BQ_LOCATION=${GCP_REGION}"
fi
```

The job's service account needs BigQuery Data Editor and BigQuery Job User.

**6d. Run the job and check the result:**

```bash
gcloud run jobs execute suffai-bq-sync --region="$GCP_REGION" --wait
bq query --location="$GCP_REGION" --use_legacy_sql=false \
  "SELECT * FROM \`${GOOGLE_CLOUD_PROJECT}.suffai_raw.ingest_runs\` ORDER BY run_at DESC LIMIT 3"
```

With the bundled offline data, expect about 19.9k orders, 39.8k order lines and 4.3k RFM rows. If the job logs `skipped N row(s) missing a required column`, the source data has rows without a key. Those rows are dropped on purpose.

## Step 7 — Verify

```bash
curl -sS "$CC_URL/health" | python3 -m json.tool
open "$CC_URL/login.html"
```

UI checks, signed in as a brand and then as admin:

| Page | Check |
|------|-------|
| Campaigns | The "Create & start campaign" bar sits above the table. The table is titled "Your 1P campaigns" and shows 10 rows per page. Changing the table filters updates Campaign insights, which carries the Simulated label |
| Geo, Logistics & warehouse | The location is a dropdown. Changing it updates the recommendation. The dropdown is disabled while an analysis runs |
| Geo, Audience | The PIN, age, gender, RFM and brand dropdowns open with Select all ticked. A brand login still sees competitors masked |
| Bot or Geo brief | When AI is off or failing, the page shows "temporarily unavailable. Reference: …" or the rule-based summary, never an exception message |

Find the full error behind any reference number shown to a user:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="suffai-command-center" AND textPayload:"reference="' \
  --limit=20 --format='value(timestamp,textPayload)'
```

## Step 8 — Rollback

Command Center:

```bash
gcloud run services update-traffic suffai-command-center --to-revisions=PREVIOUS_REVISION=100
```

Use the revision name you wrote down in Step 1. Cloud Tasks: see the revert command in Step 4. The `suffai_ops` CSV tables: re-run Step 5 with the previous CSVs.

---

## Common failures

| Symptom | Cause and fix |
|---------|---------------|
| A user sees "temporarily unavailable. Reference: abc123…" | This is expected masking. Search the logs for `reference=abc123…` (Step 7) to find the real cause |
| `/v1/metrics` returns 500 with a reference number | `command_center_data/pin_population.json` is missing from the image, or BigQuery access failed. Check the logs |
| `setup_cloud_tasks.sh`: "suffai-command-center not found" | Run Step 2 first, and check `GCP_REGION` |
| Campaign jobs stay queued after Step 4 | `cc-tasks` lacks `run.invoker`, or `COMMAND_CENTER_URL` doesn't match the service URL. Re-run the script |
| `load_to_bq.sh` fails with "CSV columns not declared in create_bq_tables.sql: X" | The CSV has an extra column. Fix the DDL or the CSV (Step 5) |
| `bq_sync` fails with "Incompatible table partitioning specification" | A table from the old job is still there. Drop it (Step 6a) and run the job again |
| `ModuleNotFoundError: google.cloud` locally | Run `pip install -r command_center/scripts/requirements.txt` |
