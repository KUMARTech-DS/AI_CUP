"""Translate GCS upload events into extraction payloads.

Supported sources:
  * Cloud Storage → Pub/Sub notification (JSON_API_V1 payload, eventType attribute)
  * Eventarc → Cloud Run (CloudEvent binary mode: object JSON body, ce-type header)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from worker.object_naming import parse_ids_from_object_key

FINALIZE_EVENTS = {"OBJECT_FINALIZE", "google.cloud.storage.object.v1.finalized"}


def is_storage_object(payload: Dict[str, Any]) -> bool:
    """True for a GCS object resource rather than our own extraction payload."""
    if not isinstance(payload, dict):
        return False
    if payload.get("gcs_object_key") or payload.get("object_key"):
        return False
    return bool(payload.get("name")) and bool(payload.get("bucket"))


def storage_event_to_payload(
    obj: Dict[str, Any],
    attributes: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Return (payload, reason). payload is None when the event should be acked and ignored.
    Only finalized image objects named useruuid_orderuuid_itemuuid.<ext> are extracted.
    """
    attrs = attributes or {}
    event_type = attrs.get("eventType")
    if event_type and event_type not in FINALIZE_EVENTS:
        return None, f"ignored event type {event_type}"

    name = obj.get("name") or attrs.get("objectId")
    bucket = obj.get("bucket") or attrs.get("bucketId")
    if not name or not bucket:
        return None, "storage event missing bucket/name"

    content_type = obj.get("contentType") or ""
    if content_type and not content_type.startswith("image/"):
        return None, f"ignored non-image object ({content_type})"

    ids = parse_ids_from_object_key(name)
    if not ids:
        return None, "object name does not match useruuid_orderuuid_itemuuid.<ext>"

    generation = obj.get("generation") or attrs.get("objectGeneration")
    key = f"gcs:{bucket}/{name}"
    if generation:
        key = f"{key}#{generation}"

    return {
        "gcs_object_key": name,
        "gcs_bucket": bucket,
        "idempotency_key": key,
        "target_type": "garment",
        "user_uuid": ids[0],
        "order_uuid": ids[1],
        "item_uuid": ids[2],
        "source": "gcs_event",
    }, "ok"
