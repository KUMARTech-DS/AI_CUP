"""Dashboard data access: BigQuery when USE_BIGQUERY_MARTS=true, else local JSON (caller falls back).

Rows are returned in the same shapes the metric functions already consume:
  * load_core()      → normalize_all() keys (customers, orders, order_commodity_lines, stores)
  * load_garments()  → GarmentStore-style garment dicts, enriched with PIN + customer demographics
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from config import settings
from suffai.garments.bq_schema import (
    COMPLAINT_TABLE,
    CUSTOMER_TABLE,
    ENRICHED_VIEW,
    GARMENT_TABLE,
    garment_row_from_extraction,
)

log = logging.getLogger("suffai.repository")


def enabled() -> bool:
    return settings.USE_BIGQUERY_MARTS and bool(settings.BQ_PROJECT or settings.GCP_PROJECT)


def _client():
    from google.cloud import bigquery

    project = settings.BQ_PROJECT or settings.GCP_PROJECT
    return bigquery.Client(project=project, location=settings.BQ_LOCATION), project


def _query(sql: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
    from google.cloud import bigquery

    client, _ = _client()
    cfg = bigquery.QueryJobConfig(query_parameters=params or [])
    return [dict(r) for r in client.query(sql, job_config=cfg).result()]


def _table(dataset: str, name: str) -> str:
    _, project = _client()
    return f"`{project}.{dataset}.{name}`"


def load_core() -> Dict[str, List[Dict[str, Any]]]:
    staging = settings.BQ_STAGING_DATASET
    customers = _query(f"SELECT * EXCEPT(source_watermark) FROM {_table(staging, CUSTOMER_TABLE)}")
    for c in customers:
        c["customer_id"] = c.get("customer_uuid")
        c["home_pin"] = c.get("pin")
    orders = _query(f"SELECT * EXCEPT(source_watermark) FROM {_table(staging, 'fact_order')}")
    lines = _query(f"SELECT * EXCEPT(source_watermark) FROM {_table(staging, 'fact_order_commodity')}")
    stores = _query(f"SELECT * EXCEPT(source_watermark) FROM {_table(staging, 'dim_store')}")
    try:
        complaints = _query(f"SELECT * EXCEPT(source_watermark) FROM {_table(staging, COMPLAINT_TABLE)}")
    except Exception as exc:  # table only exists once a sync has seen complaints
        log.info("no %s yet: %s", COMPLAINT_TABLE, exc)
        complaints = []
    log.info(
        "bq core customers=%s orders=%s lines=%s stores=%s complaints=%s",
        len(customers), len(orders), len(lines), len(stores), len(complaints),
    )
    return {
        "customers": customers,
        "orders": orders,
        "order_commodity_lines": lines,
        "stores": stores,
        "complaints": complaints,
    }


def _json_or(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def garment_from_view_row(r: Dict[str, Any]) -> Dict[str, Any]:
    """Map a v_garment_enriched row to the dashboard garment shape."""
    ingested = r.get("ingested_at")
    observed = r.get("observed_at")
    return {
        "garment_instance_id": r.get("garment_id"),
        "id": r.get("garment_id"),
        "source": "bigquery",
        "user_uuid": r.get("user_uuid"),
        "order_uuid": r.get("order_uuid"),
        "item_uuid": r.get("item_uuid"),
        "source_customer_id": r.get("user_uuid"),
        "source_order_id": r.get("order_uuid"),
        "source_order_item_id": r.get("item_uuid"),
        "order_pin_code": r.get("pin_code"),
        "order_pin": r.get("order_pin"),
        "customer_pin": r.get("customer_pin"),
        "gcs_uri": r.get("gcs_uri"),
        "image_id": r.get("image_id"),
        "commodity_type": r.get("commodity_type") or "unknown",
        "material_type": r.get("material_type") or "unknown",
        "material_blend": _json_or(r.get("material_blend"), None),
        "primary_color": r.get("primary_color") or "unknown",
        "secondary_colors": list(r.get("secondary_colors") or []),
        "size_raw": r.get("size_raw"),
        "size_normalized": r.get("size_normalized"),
        "size_system": r.get("size_system"),
        "brand_name_raw": r.get("brand_name") or "unknown",
        "brand_sku": r.get("brand_sku"),
        "pattern": r.get("pattern") or "unknown",
        "gender": r.get("gender") or "unknown",
        "style_tags": list(r.get("style_tags") or []),
        "attribute_evidence": _json_or(r.get("attribute_evidence"), {}),
        "notes": r.get("notes"),
        "model_version": r.get("model_version"),
        "review_status": (r.get("review_status") or "needs_review").lower(),
        "observed_at": observed.isoformat() if hasattr(observed, "isoformat") else observed,
        "updated_at": ingested.isoformat() if hasattr(ingested, "isoformat") else ingested,
        "customer_gender": r.get("customer_gender"),
        "customer_age": r.get("customer_age"),
        "customer_age_band": r.get("customer_age_band") or "unknown",
        "customer_device_type": r.get("customer_device_type"),
        "deleted_at": None,
    }


def load_garments(limit: int = 200_000) -> List[Dict[str, Any]]:
    rows = _query(
        f"SELECT * FROM {_table(settings.BQ_MARTS_DATASET, ENRICHED_VIEW)} "
        f"ORDER BY ingested_at DESC LIMIT {int(limit)}"
    )
    log.info("bq garments=%s", len(rows))
    return [garment_from_view_row(r) for r in rows]


def append_review_row(
    garment: Dict[str, Any],
    attributes: Dict[str, Any],
    *,
    review_status: Optional[str],
    actor: str,
) -> Dict[str, Any]:
    """Human correction = new observation row; v_garment_enriched picks the latest per garment."""
    merged = {**garment, **attributes}
    if "brand_name_raw" in attributes:
        merged["brand_name"] = attributes["brand_name_raw"]
    else:
        merged["brand_name"] = garment.get("brand_name_raw")
    merged["review_status"] = (review_status or garment.get("review_status") or "needs_review").lower()
    merged["notes"] = f"human_review by {actor}"
    row = garment_row_from_extraction(
        observation_id=f"review:{garment.get('garment_instance_id')}:{uuid.uuid4()}",
        result=merged,
        status="COMPLETED" if merged["review_status"] == "completed" else "NEEDS_REVIEW",
        user_uuid=garment.get("user_uuid"),
        order_uuid=garment.get("order_uuid"),
        item_uuid=garment.get("item_uuid"),
        gcs_uri=garment.get("gcs_uri"),
        image_id=garment.get("image_id"),
        source="human_review",
        actor=actor,
    )
    client, project = _client()
    table_id = f"{project}.{settings.BQ_STAGING_DATASET}.{GARMENT_TABLE}"
    errors = client.insert_rows_json(table_id, [row], row_ids=[row["observation_id"]])
    if errors:
        raise RuntimeError(f"BigQuery review insert failed: {errors}")
    return row
