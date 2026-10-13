"""
Cloud Run Job: sync normalized facts + marts into BigQuery (GCP-native warehouse).

  python -m jobs.bq_sync
Env: GOOGLE_CLOUD_PROJECT, BQ_* datasets, DATA_DIR
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
from suffai.ingest import load_source_collections, normalize_all
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


def _load_rows(client, table_id: str, rows: list[dict], write="WRITE_TRUNCATE") -> int:
    from google.cloud import bigquery

    if not rows:
        log.info("skip empty %s", table_id)
        return 0
    job_config = bigquery.LoadJobConfig(
        write_disposition=getattr(bigquery.WriteDisposition, write),
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True,
    )
    # serialize dates
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
    raw = load_source_collections(settings.DATA_DIR)
    norm = normalize_all(raw)
    watermark = datetime.now(timezone.utc).isoformat()

    staging = settings.BQ_STAGING_DATASET
    marts = settings.BQ_MARTS_DATASET

    orders = [{**o, "source_watermark": watermark} for o in norm["orders"]]
    lines = [{**l, "source_watermark": watermark} for l in norm["order_commodity_lines"]]
    _load_rows(client, f"{project}.{staging}.fact_order", orders)
    _load_rows(client, f"{project}.{staging}.fact_order_commodity", lines)

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
    return {"watermark": watermark, "orders": len(orders), "lines": len(lines), "rfm": len(rfm)}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
