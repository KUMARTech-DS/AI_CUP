"""
Cloud Run Job: sync normalized facts + marts into BigQuery (GCP-native warehouse).

  python -m jobs.bq_sync
Env: GOOGLE_CLOUD_PROJECT, BQ_* datasets, GCS_RAW_BUCKET + GCS_DATA_PREFIX (DATA_DIR locally)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import settings
from suffai.garments.bq_schema import (
    COMPLAINT_FIELDS,
    COMPLAINT_TABLE,
    CUSTOMER_FIELDS,
    CUSTOMER_TABLE,
    ORDER_FIELDS,
    ORDER_LINE_FIELDS,
    STORE_FIELDS,
    bq_schema,
    enriched_view_sql,
    ensure_garment_table,
)
from suffai.gcp_io import source_data_location
from suffai.ingest import load_source_collections, normalize_all
from suffai.metrics.demographics import age_band
from suffai.metrics.eligibility import eligible_orders
from suffai.metrics.geo import geo_active_customers, commodity_popularity
from suffai.metrics.rfm import compute_rfm_snapshots, rfm_distribution

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("suffai.bq_sync")


def _client():
    from google.cloud import bigquery

    project = settings.BQ_PROJECT or settings.GCP_PROJECT
    if not project:
        raise RuntimeError("BQ_PROJECT / GOOGLE_CLOUD_PROJECT required")
    return bigquery.Client(project=project, location=settings.BQ_LOCATION), project


def _ensure_datasets(client, project: str) -> None:
    from google.cloud import bigquery

    for ds in (settings.BQ_RAW_DATASET, settings.BQ_STAGING_DATASET, settings.BQ_MARTS_DATASET):
        ref = bigquery.Dataset(f"{project}.{ds}")
        ref.location = settings.BQ_LOCATION
        client.create_dataset(ref, exists_ok=True)


def _coerce(value, typ: str):
    if value is None:
        return None
    try:
        if typ == "STRING":
            return value if isinstance(value, str) else (
                json.dumps(value, default=str) if isinstance(value, (list, dict)) else str(value)
            )
        if typ == "INT64":
            return int(value)
        if typ == "FLOAT64":
            return float(value)
        if typ == "BOOL":
            return bool(value)
        if typ == "TIMESTAMP":
            return value.isoformat() if hasattr(value, "isoformat") else str(value)
    except (TypeError, ValueError):
        return None
    return value


def _load_rows(client, table_id: str, rows: list[dict], write="WRITE_TRUNCATE", fields=None) -> int:
    """Load rows; with `fields`, use an explicit schema so ids / PINs stay STRING."""
    from google.cloud import bigquery

    if not rows:
        log.info("skip empty %s", table_id)
        return 0
    job_config = bigquery.LoadJobConfig(
        write_disposition=getattr(bigquery.WriteDisposition, write),
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    if fields:
        job_config.schema = bq_schema(fields)
        job_config.ignore_unknown_values = True
        types = {name: typ for name, typ, _ in fields}
        payload = [{k: _coerce(r.get(k), t) for k, t in types.items()} for r in rows]
    else:
        job_config.autodetect = True
        payload = []
        for r in rows:
            item = {}
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    item[k] = v.isoformat()
                else:
                    item[k] = v
            payload.append(item)
    job = client.load_table_from_json(payload, table_id, job_config=job_config)
    job.result()
    log.info("loaded %s rows=%s", table_id, len(payload))
    return len(payload)


def run() -> dict:
    client, project = _client()
    _ensure_datasets(client, project)
    raw = load_source_collections(source_data_location())
    norm = normalize_all(raw)
    watermark = datetime.now(timezone.utc).isoformat()

    staging = settings.BQ_STAGING_DATASET
    marts = settings.BQ_MARTS_DATASET

    orders = [{**o, "source_watermark": watermark} for o in norm["orders"]]
    lines = [{**l, "source_watermark": watermark} for l in norm["order_commodity_lines"]]
    _load_rows(client, f"{project}.{staging}.fact_order", orders, fields=ORDER_FIELDS)
    _load_rows(client, f"{project}.{staging}.fact_order_commodity", lines, fields=ORDER_LINE_FIELDS)
    _load_rows(
        client,
        f"{project}.{staging}.dim_store",
        [{**s, "source_watermark": watermark} for s in norm["stores"]],
        fields=STORE_FIELDS,
    )
    # Demographics only: phone_number / address stay in the Cloud SQL customers table.
    customers = [
        {
            "customer_uuid": c.get("customer_uuid") or c.get("customer_id"),
            "user_id": c.get("user_id"),
            "gender": c.get("gender"),
            "age": c.get("age"),
            "age_band": age_band(c.get("age")),
            "pin": c.get("pin") or c.get("home_pin"),
            "device_type": c.get("device_type"),
            "home_lat_cell": c.get("home_lat_cell"),
            "home_lng_cell": c.get("home_lng_cell"),
            "created_at": c.get("created_at"),
            "source_watermark": watermark,
        }
        for c in norm["customers"]
        if c.get("customer_uuid") or c.get("customer_id")
    ]
    _load_rows(client, f"{project}.{staging}.{CUSTOMER_TABLE}", customers, fields=CUSTOMER_FIELDS)
    complaints = [{**c, "source_watermark": watermark} for c in norm.get("complaints", [])]
    _load_rows(client, f"{project}.{staging}.{COMPLAINT_TABLE}", complaints, fields=COMPLAINT_FIELDS)

    # Garment observations are streamed by suffai-extraction; make sure table + view exist.
    ensure_garment_table(client, project, staging, settings.BQ_LOCATION)
    client.query(enriched_view_sql(project, staging, marts)).result()
    log.info("ensured %s.%s.v_garment_enriched", project, marts)

    rfm = compute_rfm_snapshots(norm["orders"], norm["stores"])
    _load_rows(
        client,
        f"{project}.{staging}.fact_customer_segment_snapshot",
        [{**s, "source_watermark": watermark} for s in rfm],
    )

    tiles = geo_active_customers(norm["orders"])
    eligible_ids = {o["order_id"] for o in eligible_orders(norm["orders"])}
    commodities = commodity_popularity(norm["order_commodity_lines"], eligible_ids)
    _load_rows(
        client,
        f"{project}.{marts}.mart_geo_trends",
        [
            {
                "aggregate_key": f"{t.get('geo_key')}-active_customers",
                "geo_key": t.get("geo_key"),
                "metric_key": "active_customers",
                "metric_value": t.get("active_customers"),
                "cohort_size": t.get("denominator"),
                "suppressed": t.get("suppressed"),
                "source_watermark": watermark,
            }
            for t in tiles
        ],
    )
    _load_rows(
        client,
        f"{project}.{marts}.mart_commodity_popularity",
        [{**c, "source_watermark": watermark} for c in commodities[:500]],
    )
    _load_rows(
        client,
        f"{project}.{marts}.mart_rfm_distribution",
        [{**r, "source_watermark": watermark} for r in rfm_distribution(rfm)],
    )

    # raw snapshot marker
    _load_rows(
        client,
        f"{project}.{settings.BQ_RAW_DATASET}.ingest_runs",
        [{"run_at": watermark, "orders": len(orders), "lines": len(lines), "rfm": len(rfm)}],
        write="WRITE_APPEND",
    )
    return {
        "watermark": watermark,
        "orders": len(orders),
        "lines": len(lines),
        "customers": len(customers),
        "complaints": len(complaints),
        "rfm": len(rfm),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
