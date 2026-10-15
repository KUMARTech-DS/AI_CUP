"""Append extraction results to BigQuery fact_garment_observation."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from suffai.garments.bq_schema import ensure_garment_table
from worker.settings import settings

log = logging.getLogger("suffai-extraction.bq")

_client = None
_table_id: Optional[str] = None


def enabled() -> bool:
    return settings.ENABLE_BIGQUERY and bool(settings.BQ_PROJECT)


def _table():
    global _client, _table_id
    if _client is None:
        from google.cloud import bigquery

        _client = bigquery.Client(project=settings.BQ_PROJECT, location=settings.BQ_LOCATION)
    if _table_id is None:
        _table_id = ensure_garment_table(
            _client, settings.BQ_PROJECT, settings.BQ_STAGING_DATASET, settings.BQ_LOCATION
        )
    return _client, _table_id


def insert_garment_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Streaming insert; row_ids=observation_id lets BigQuery drop Pub/Sub redelivery duplicates."""
    client, table_id = _table()
    errors = client.insert_rows_json(table_id, [row], row_ids=[row["observation_id"]])
    if errors:
        raise RuntimeError(f"BigQuery insert failed: {errors}")
    log.info(
        "bq_insert table=%s garment_id=%s review_status=%s",
        table_id,
        row.get("garment_id"),
        row.get("review_status"),
    )
    return {"table": table_id, "observation_id": row["observation_id"], "garment_id": row.get("garment_id")}
