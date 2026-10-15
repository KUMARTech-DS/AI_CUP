# suffai-extraction (image worker)

Cloud Run service that:

1. Receives a **GCS upload event** for `useruuid_orderuuid_itemuuid.jpg` (or an API-published payload)
2. Extracts attributes + garment gender with **Vertex AI Gemini**
3. Appends one row to **BigQuery** `suffai_staging.fact_garment_observation` (system of record read by dashboards)
4. Best-effort Cloud SQL write (`extraction_jobs`, `garment_instances`, `campaigns.metadata`) when `DATABASE_URL` is set

## Data flow to the dashboard

```text
GCS upload  ──notification──▶ Pub/Sub ──push──▶ suffai-extraction ──▶ BigQuery fact_garment_observation
JSON orders/customers ──jobs.bq_sync──▶ BigQuery fact_order, dim_customer, dim_store
                     suffai_marts.v_garment_enriched = latest garment row + order PIN + customer demographics
suffai-api (USE_BIGQUERY_MARTS=true) reads the view → Geo / Brand / Research / Review tabs
```

The view attributes each garment to the order PIN (`order_uuid` → `fact_order.order_pin`),
falling back to the customer home PIN (`user_uuid` → `dim_customer.pin`). Human corrections from
the Review tab append a new row (`source=human_review`); the view always takes the latest row.

### One-time setup: GCS → Pub/Sub notification

```bash
gcloud storage buckets notifications create gs://$GCS_IMAGE_BUCKET \
  --topic=garment-image-uploaded --event-types=OBJECT_FINALIZE --payload-format=json
```

The existing push subscription to `suffai-extraction` handles these messages. Objects whose name
does not match `useruuid_orderuuid_itemuuid.<jpg|png|webp>` are acknowledged and ignored, so
campaign creatives and API-flow uploads are not processed twice. Eventarc
(`google.cloud.storage.object.v1.finalized`) delivered directly to the service also works.

### One-time setup: BigQuery

Run the `suffai-bq-sync` job once (`python -m jobs.bq_sync`). It loads orders / customers / stores,
creates `fact_garment_observation` if missing, and creates `v_garment_enriched`. The worker also
creates the table on first insert.

## Env vars (Cloud Run → `suffai-extraction`)

| Name | Example | Required |
|------|---------|----------|
| `PORT` | `8080` | yes (Cloud Run) |
| `GOOGLE_CLOUD_PROJECT` | your project id | yes for Vertex |
| `GCS_IMAGE_BUCKET` | `{PROJECT}-suffai-garment-images` | yes |
| `ENABLE_VERTEX` | `true` | yes to call Gemini |
| `VERTEX_LOCATION` | `asia-south1` | recommended |
| `VERTEX_MODEL` | `gemini-2.0-flash` | optional |
| `ENABLE_BIGQUERY` | `true` (default) | yes |
| `BQ_PROJECT` | defaults to `GOOGLE_CLOUD_PROJECT` | optional |
| `BQ_STAGING_DATASET` | `suffai_staging` | optional |
| `BQ_LOCATION` | `asia-south1` | optional |
| `DATABASE_URL` | see below | optional (Cloud SQL mirror) |
| `AUTO_ACCEPT_CONFIDENCE` | `0.90` | optional |

### `DATABASE_URL` (Cloud SQL unix socket)

```text
postgresql://USER:PASSWORD@/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE
```

Also attach the Cloud SQL instance on the Cloud Run service (`suffai-ops` or your instance id).

## IAM (worker runtime SA)

- `roles/storage.objectViewer` on the image bucket
- `roles/aiplatform.user` (Vertex)
- `roles/bigquery.dataEditor` on `suffai_staging` + `roles/bigquery.jobUser` on the project
- Cloud SQL Client on the instance (only if `DATABASE_URL` is set)

`suffai-api` runtime SA needs `roles/bigquery.dataViewer` on `suffai_staging` and `suffai_marts`,
`roles/bigquery.dataEditor` on `suffai_staging` (Review tab corrections), and `roles/bigquery.jobUser`.
The GCS service agent needs `roles/pubsub.publisher` on the topic for bucket notifications.
- Secret access if password comes from Secret Manager

## Pub/Sub payload (`garment-image-uploaded`)

```json
{
  "gcs_object_key": "garments/order123/item1.jpg",
  "gcs_bucket": "optional-override",
  "image_id": "uuid-of-garment_images-row",
  "garment_instance_id": "uuid-of-garment_instances-row",
  "idempotency_key": "unique-per-upload",
  "target_type": "garment"
}
```

Campaign creative:

```json
{
  "gcs_object_key": "campaigns/creative1.jpg",
  "campaign_id": "uuid-of-campaigns-row",
  "target_type": "campaign",
  "idempotency_key": "campaign-extract-1"
}
```

## Manual test (no Pub/Sub)

```bash
WORKER_URL=$(gcloud run services describe suffai-extraction --region=asia-south1 --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)

curl -sS -X POST "$WORKER_URL/v1/extract" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "gcs_object_key": "garments/sample.jpg",
    "garment_instance_id": "YOUR-GARMENT-UUID",
    "image_id": "YOUR-IMAGE-UUID",
    "idempotency_key": "manual-test-1",
    "target_type": "garment"
  }'
```

## Rebuild / redeploy only the worker

```bash
gcloud builds submit --config=deploy/cloudbuild-worker.yaml .
# then deploy image asia-south1-docker.pkg.dev/$PROJECT/suffai/suffai-extraction:latest
# to Cloud Run service suffai-extraction with the env vars above
```

Apply SQL once if not already applied: `db/migrations/001_operational.sql`.
