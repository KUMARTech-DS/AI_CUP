"""Optional BigQuery sync (same project/dataset conventions as command_center)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from similarity.settings import settings

log = logging.getLogger("similarity.bq")


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
