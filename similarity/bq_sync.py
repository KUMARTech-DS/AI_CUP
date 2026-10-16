"""BigQuery helpers: customers PIN lookup + optional observation sync."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from similarity.settings import settings

log = logging.getLogger("similarity.bq")


def lookup_pin_for_user(user_uuid: str) -> Optional[str]:
    """
    Resolve pin_code from suffai_ops.customers (same table as command center).
    Matches customers._id to the useruuid from the image filename.
    """
    if not user_uuid:
        return None
    if not settings.PROJECT_ID:
        log.warning("GOOGLE_CLOUD_PROJECT unset; cannot lookup PIN")
        return None
    try:
        from google.cloud import bigquery
    except ImportError:
        log.exception("google-cloud-bigquery not installed")
        return None

    client = bigquery.Client(project=settings.PROJECT_ID)
    table = f"`{settings.PROJECT_ID}.{settings.BQ_DATASET}.customers`"
    sql = f"""
      SELECT CAST(pin_code AS STRING) AS pin_code
      FROM {table}
      WHERE CAST(_id AS STRING) = @uid
      LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_uuid)]
    )
    try:
        rows = list(client.query(sql, job_config=job_config).result())
    except Exception:
        log.exception("customers PIN lookup failed user=%s", user_uuid)
        return None
    if not rows:
        log.warning("no customers row for user_uuid=%s", user_uuid)
        return None
    pin = (rows[0]["pin_code"] or "").strip()
    log.info("PIN lookup user=%s pin=%s", user_uuid, pin)
    return pin or None


def sync_observation(
    *,
    garment_id: str,
    user_uuid: str,
    pin_code: str,
    brand_name: Optional[str],
    outcome: str,
    gcs_object_key: str,
    similarity_score: Optional[float],
) -> None:
    """Append one observation row to BQ when USE_BQ=true (analytics; Cloud SQL remains SoT)."""
    if not settings.USE_BQ:
        return
    if not settings.PROJECT_ID:
        log.warning("USE_BQ=true but GOOGLE_CLOUD_PROJECT unset; skip BQ sync")
        return

    try:
        from google.cloud import bigquery
    except ImportError:
        log.exception("google-cloud-bigquery not installed")
        return

    client = bigquery.Client(project=settings.PROJECT_ID)
    table_id = f"{settings.PROJECT_ID}.{settings.BQ_DATASET}.garment_similarity_observations"
    row: dict[str, Any] = {
        "event_ts": datetime.now(timezone.utc).isoformat(),
        "garment_id": garment_id,
        "user_uuid": user_uuid,
        "pin_code": pin_code,
        "brand_name": brand_name,
        "outcome": outcome,
        "gcs_object_key": gcs_object_key,
        "similarity_score": similarity_score,
    }
    errors = client.insert_rows_json(table_id, [row])
    if errors:
        log.error("BQ insert errors table=%s errors=%s", table_id, errors)
    else:
        log.info("BQ sync ok table=%s garment=%s pin=%s", table_id, garment_id, pin_code)
