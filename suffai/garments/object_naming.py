"""GCS object naming: {user_uuid}_{order_uuid}_{item_uuid}.jpg"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

# Opaque ids without underscores (UUIDs with hyphens are fine).
_ID_PART = r"[A-Za-z0-9-]{1,128}"
_OBJECT_RE = re.compile(
    rf"^({_ID_PART})_({_ID_PART})_({_ID_PART})\.(jpe?g|png|webp)$",
    re.IGNORECASE,
)


def build_garment_object_key(
    user_uuid: str,
    order_uuid: str,
    item_uuid: str,
    *,
    content_type: str = "image/jpeg",
) -> str:
    """Build flat GCS object key: useruuid_orderuuid_itemuuid.<ext>."""
    user = _sanitize_id(user_uuid)
    order = _sanitize_id(order_uuid)
    item = _sanitize_id(item_uuid)
    ext = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get((content_type or "").lower(), "jpg")
    return f"{user}_{order}_{item}.{ext}"


def _sanitize_id(value: str) -> str:
    cleaned = (value or "").strip().replace("/", "").replace("\\", "")
    if not cleaned:
        raise ValueError("user/order/item id must be non-empty")
    if "_" in cleaned:
        raise ValueError(
            "user/order/item ids must not contain underscores "
            "(required for useruuid_orderuuid_itemuuid.jpg naming)"
        )
    return cleaned


def parse_ids_from_object_key(object_key: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse basename useruuid_orderuuid_itemuuid.<ext>.
    Returns (user_uuid, order_uuid, item_uuid) or None.
    """
    if not object_key:
        return None
    basename = os.path.basename(object_key.strip())
    match = _OBJECT_RE.match(basename)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def resolve_source_ids(
    payload: Dict[str, Any], object_key: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """Prefer explicit payload fields; fall back to GCS object key naming."""
    user = (
        payload.get("user_uuid")
        or payload.get("useruuid")
        or payload.get("source_customer_id")
        or payload.get("source_user_id")
    )
    order = (
        payload.get("order_uuid")
        or payload.get("orderuuid")
        or payload.get("source_order_id")
    )
    item = (
        payload.get("item_uuid")
        or payload.get("itemuuid")
        or payload.get("source_order_item_id")
    )

    if not (user and order and item) and object_key:
        parsed = parse_ids_from_object_key(object_key)
        if parsed:
            user = user or parsed[0]
            order = order or parsed[1]
            item = item or parsed[2]

    return {
        "user_uuid": str(user) if user else None,
        "order_uuid": str(order) if order else None,
        "item_uuid": str(item) if item else None,
    }
