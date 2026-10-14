"""BigQuery layout for extracted garment observations (shared by worker, bq_sync, API).

Tables / views (project from env, datasets configurable):
  {staging}.fact_garment_observation  append-only; one row per extraction or human review
  {staging}.dim_customer              demographics only (no phone / address)
  {staging}.fact_order                normalized orders (order_pin)
  {marts}.v_garment_enriched          latest row per garment + PIN + customer demographics

Only depends on google-cloud-bigquery (imported lazily) so the worker image can use it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

GARMENT_TABLE = "fact_garment_observation"
CUSTOMER_TABLE = "dim_customer"
ENRICHED_VIEW = "v_garment_enriched"

_GARMENT_NS = uuid.UUID("6f1f7a52-3c8e-4f0e-9a51-5f2b9d1c0a11")

# (name, type, mode)
GARMENT_FIELDS = [
    ("observation_id", "STRING", "REQUIRED"),
    ("garment_id", "STRING", "NULLABLE"),
    ("user_uuid", "STRING", "NULLABLE"),
    ("order_uuid", "STRING", "NULLABLE"),
    ("item_uuid", "STRING", "NULLABLE"),
    ("gcs_uri", "STRING", "NULLABLE"),
    ("gcs_object_key", "STRING", "NULLABLE"),
    ("content_type", "STRING", "NULLABLE"),
    ("size_bytes", "INT64", "NULLABLE"),
    ("image_id", "STRING", "NULLABLE"),
    ("commodity_type", "STRING", "NULLABLE"),
    ("material_type", "STRING", "NULLABLE"),
    ("material_blend", "STRING", "NULLABLE"),
    ("primary_color", "STRING", "NULLABLE"),
    ("secondary_colors", "STRING", "REPEATED"),
    ("size_raw", "STRING", "NULLABLE"),
    ("size_normalized", "STRING", "NULLABLE"),
    ("size_system", "STRING", "NULLABLE"),
    ("brand_name", "STRING", "NULLABLE"),
    ("brand_sku", "STRING", "NULLABLE"),
    ("pattern", "STRING", "NULLABLE"),
    ("gender", "STRING", "NULLABLE"),
    ("style_tags", "STRING", "REPEATED"),
    ("attribute_evidence", "STRING", "NULLABLE"),
    ("notes", "STRING", "NULLABLE"),
    ("model_version", "STRING", "NULLABLE"),
    ("review_status", "STRING", "NULLABLE"),
    ("extraction_status", "STRING", "NULLABLE"),
    ("source", "STRING", "NULLABLE"),
    ("actor", "STRING", "NULLABLE"),
    ("observed_at", "TIMESTAMP", "NULLABLE"),
    ("ingested_at", "TIMESTAMP", "REQUIRED"),
]

CUSTOMER_FIELDS = [
    ("customer_uuid", "STRING", "REQUIRED"),
    ("user_id", "STRING", "NULLABLE"),
    ("gender", "STRING", "NULLABLE"),
    ("age", "INT64", "NULLABLE"),
    ("age_band", "STRING", "NULLABLE"),
    ("pin", "STRING", "NULLABLE"),
    ("device_type", "STRING", "NULLABLE"),
    # ~1 km grid cell from the geo address, for the demand heatmap; never the raw coordinates.
    ("home_lat_cell", "FLOAT64", "NULLABLE"),
    ("home_lng_cell", "FLOAT64", "NULLABLE"),
    ("created_at", "TIMESTAMP", "NULLABLE"),
    ("source_watermark", "TIMESTAMP", "NULLABLE"),
]

ORDER_FIELDS = [
    ("order_id", "STRING", "REQUIRED"),
    ("customer_id", "STRING", "NULLABLE"),
    ("store_id", "STRING", "NULLABLE"),
    ("bill_no", "STRING", "NULLABLE"),
    ("order_status", "STRING", "NULLABLE"),
    ("payment_status", "STRING", "NULLABLE"),
    ("refund_flag", "BOOL", "NULLABLE"),
    ("total_amount", "FLOAT64", "NULLABLE"),
    ("sub_total_amount", "FLOAT64", "NULLABLE"),
    ("ordered_at", "TIMESTAMP", "NULLABLE"),
    ("order_pin", "STRING", "NULLABLE"),
    ("total_number_of_clothes", "INT64", "NULLABLE"),
    ("source_watermark", "TIMESTAMP", "NULLABLE"),
]

ORDER_LINE_FIELDS = [
    ("line_id", "STRING", "NULLABLE"),
    ("order_id", "STRING", "REQUIRED"),
    ("order_item_id", "STRING", "NULLABLE"),
    ("customer_id", "STRING", "NULLABLE"),
    ("store_id", "STRING", "NULLABLE"),
    ("service_id", "STRING", "NULLABLE"),
    ("service_name", "STRING", "NULLABLE"),
    ("catalog_commodity_id", "STRING", "NULLABLE"),
    ("commodity_name", "STRING", "NULLABLE"),
    ("menu", "STRING", "NULLABLE"),
    ("quantity", "INT64", "NULLABLE"),
    ("line_price", "FLOAT64", "NULLABLE"),
    ("order_pin", "STRING", "NULLABLE"),
    ("ordered_at", "TIMESTAMP", "NULLABLE"),
    ("source_watermark", "TIMESTAMP", "NULLABLE"),
]

COMPLAINT_TABLE = "fact_complaint"
COMPLAINT_FIELDS = [
    ("complaint_id", "STRING", "REQUIRED"),
    ("order_id", "STRING", "NULLABLE"),
    ("customer_id", "STRING", "NULLABLE"),
    ("store_id", "STRING", "NULLABLE"),
    ("complaint_status", "STRING", "NULLABLE"),
    ("resolved", "BOOL", "NULLABLE"),
    ("created_at", "TIMESTAMP", "NULLABLE"),
    ("source_watermark", "TIMESTAMP", "NULLABLE"),
]

STORE_FIELDS = [
    ("store_id", "STRING", "REQUIRED"),
    ("store_name", "STRING", "NULLABLE"),
    ("pin_code", "STRING", "NULLABLE"),
    ("city", "STRING", "NULLABLE"),
    ("area", "STRING", "NULLABLE"),
    ("premium_laundry", "BOOL", "NULLABLE"),
    ("store_category", "STRING", "NULLABLE"),
    ("status", "STRING", "NULLABLE"),
    ("source_watermark", "TIMESTAMP", "NULLABLE"),
]


def bq_schema(fields):
    from google.cloud import bigquery

    return [bigquery.SchemaField(name, typ, mode=mode) for name, typ, mode in fields]


def garment_id_for(user_uuid: Optional[str], order_uuid: Optional[str], item_uuid: Optional[str]) -> Optional[str]:
    """Deterministic garment key so re-extractions and reviews collapse to one garment."""
    if not (user_uuid and order_uuid and item_uuid):
        return None
    return str(uuid.uuid5(_GARMENT_NS, f"{user_uuid}|{order_uuid}|{item_uuid}"))


def _iso(ts: Optional[datetime]) -> str:
    return (ts or datetime.now(timezone.utc)).isoformat()


def _str_list(val: Any) -> list[str]:
    if not isinstance(val, list):
        return []
    return [str(v) for v in val if v is not None]


def garment_row_from_extraction(
    *,
    observation_id: str,
    result: Dict[str, Any],
    status: str,
    user_uuid: Optional[str] = None,
    order_uuid: Optional[str] = None,
    item_uuid: Optional[str] = None,
    gcs_uri: Optional[str] = None,
    gcs_object_key: Optional[str] = None,
    content_type: Optional[str] = None,
    size_bytes: Optional[int] = None,
    image_id: Optional[str] = None,
    source: str = "extraction",
    actor: Optional[str] = None,
    observed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Flatten a worker / review result into a fact_garment_observation row (JSON-safe)."""
    blend = result.get("material_blend")
    evidence = result.get("attribute_evidence")
    review = (result.get("review_status") or status or "needs_review").lower()
    now = _iso(None)
    return {
        "observation_id": observation_id,
        "garment_id": garment_id_for(user_uuid, order_uuid, item_uuid),
        "user_uuid": user_uuid,
        "order_uuid": order_uuid,
        "item_uuid": item_uuid,
        "gcs_uri": gcs_uri,
        "gcs_object_key": gcs_object_key,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "image_id": image_id,
        "commodity_type": result.get("commodity_type"),
        "material_type": result.get("material_type"),
        "material_blend": json.dumps(blend) if blend is not None else None,
        "primary_color": result.get("primary_color"),
        "secondary_colors": _str_list(result.get("secondary_colors")),
        "size_raw": result.get("size_raw"),
        "size_normalized": result.get("size_normalized"),
        "size_system": result.get("size_system"),
        "brand_name": result.get("brand_name") or result.get("brand_name_raw"),
        "brand_sku": result.get("brand_sku"),
        "pattern": result.get("pattern"),
        "gender": result.get("gender"),
        "style_tags": _str_list(result.get("style_tags")),
        "attribute_evidence": json.dumps(evidence) if evidence is not None else None,
        "notes": result.get("notes"),
        "model_version": result.get("model_version"),
        "review_status": review,
        "extraction_status": status,
        "source": source,
        "actor": actor,
        "observed_at": _iso(observed_at),
        "ingested_at": now,
    }


def ensure_garment_table(client, project: str, dataset: str, location: str) -> str:
    """Create dataset + partitioned/clustered garment table if missing. Returns table id."""
    from google.cloud import bigquery

    ds = bigquery.Dataset(f"{project}.{dataset}")
    ds.location = location
    client.create_dataset(ds, exists_ok=True)

    table_id = f"{project}.{dataset}.{GARMENT_TABLE}"
    table = bigquery.Table(table_id, schema=bq_schema(GARMENT_FIELDS))
    table.time_partitioning = bigquery.TimePartitioning(field="observed_at")
    table.clustering_fields = ["user_uuid", "order_uuid", "garment_id"]
    client.create_table(table, exists_ok=True)
    return table_id


def enriched_view_sql(project: str, staging: str, marts: str) -> str:
    """Latest observation per garment, attributed to order PIN (fallback: customer home PIN)."""
    return f"""
CREATE OR REPLACE VIEW `{project}.{marts}.{ENRICHED_VIEW}` AS
WITH latest AS (
  SELECT *
  FROM `{project}.{staging}.{GARMENT_TABLE}`
  WHERE garment_id IS NOT NULL
    AND COALESCE(extraction_status, '') != 'FAILED'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY garment_id ORDER BY ingested_at DESC) = 1
)
SELECT
  g.*,
  o.order_pin,
  c.pin AS customer_pin,
  COALESCE(o.order_pin, c.pin) AS pin_code,
  c.gender AS customer_gender,
  c.age AS customer_age,
  c.age_band AS customer_age_band,
  c.device_type AS customer_device_type
FROM latest g
LEFT JOIN `{project}.{staging}.fact_order` o ON o.order_id = g.order_uuid
LEFT JOIN `{project}.{staging}.{CUSTOMER_TABLE}` c ON c.customer_uuid = g.user_uuid
"""
