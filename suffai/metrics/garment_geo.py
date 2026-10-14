"""Extracted garment attributes by PIN (user → order/customer PIN), k-suppressed."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from suffai import METRIC_VERSION, PRIVACY_K
from suffai.metrics.demographics import enrich_garments_with_customers
from suffai.metrics.geo import PIN_AREA_LABELS

GARMENT_ATTRS = (
    "commodity_type",
    "primary_color",
    "material_type",
    "pattern",
    "gender",
    "brand_name_raw",
)


def _matches(value: Any, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    return str(value or "unknown").lower() in {a.lower() for a in allowed}


def _cid(g: dict[str, Any]) -> str | None:
    return g.get("source_customer_id") or g.get("user_uuid")


def garment_pin_summary(
    garments: list[dict[str, Any]],
    *,
    customers_by_id: dict[str, dict[str, Any]] | None = None,
    geo_ids: list[str] | None = None,
    genders: list[str] | None = None,
    age_bands: list[str] | None = None,
    device_types: list[str] | None = None,
    k: int = PRIVACY_K,
) -> dict[str, Any]:
    rows = [
        g for g in garments
        if not g.get("deleted_at") and g.get("review_status") in {"completed", "needs_review"}
    ]
    if customers_by_id:
        rows = enrich_garments_with_customers(rows, customers_by_id)
    rows = [
        g for g in rows
        if _matches(g.get("customer_gender"), genders)
        and _matches(g.get("customer_age_band"), age_bands)
        and _matches(g.get("customer_device_type"), device_types)
    ]
    if geo_ids:
        rows = [g for g in rows if (g.get("order_pin_code") or "UNKNOWN") in geo_ids]

    by_pin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in rows:
        by_pin[g.get("order_pin_code") or "UNKNOWN"].append(g)

    def top_values(items: list[dict[str, Any]], field: str, n: int = 3) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        for g in items:
            counts[str(g.get(field) or "unknown")] += 1
        return [
            {"value": v, "garments": c}
            for v, c in sorted(counts.items(), key=lambda x: -x[1])
            if v != "unknown"
        ][:n]

    pins = []
    for pin, items in sorted(by_pin.items(), key=lambda x: -len(x[1])):
        customers = {c for c in (_cid(g) for g in items) if c}
        suppressed = len(customers) < k
        pins.append({
            "pin_code": None if pin == "UNKNOWN" else pin,
            "geo_key": pin,
            "area": PIN_AREA_LABELS.get(pin, "Unknown" if pin == "UNKNOWN" else pin),
            "garments": None if suppressed else len(items),
            "customers": None if suppressed else len(customers),
            "reviewed_garments": None if suppressed else sum(
                1 for g in items if g.get("review_status") == "completed"
            ),
            "suppressed": suppressed,
            "top": {} if suppressed else {f: top_values(items, f) for f in GARMENT_ATTRS},
        })

    overall: dict[str, list[dict[str, Any]]] = {}
    for field in GARMENT_ATTRS:
        cust_by_value: dict[str, set[str]] = defaultdict(set)
        count_by_value: dict[str, int] = defaultdict(int)
        for g in rows:
            val = str(g.get(field) or "unknown")
            count_by_value[val] += 1
            if _cid(g):
                cust_by_value[val].add(_cid(g))
        overall[field] = [
            {
                "value": v,
                "garments": None if len(cust_by_value[v]) < k else n,
                "customers": None if len(cust_by_value[v]) < k else len(cust_by_value[v]),
                "suppressed": len(cust_by_value[v]) < k,
            }
            for v, n in sorted(count_by_value.items(), key=lambda x: -x[1])
        ]

    all_customers = {c for c in (_cid(g) for g in rows) if c}
    total_suppressed = len(all_customers) < k
    return {
        "by_pin": pins,
        "overall": overall,
        "garments_total": None if total_suppressed else len(rows),
        "customers_total": None if total_suppressed else len(all_customers),
        "reviewed_garments": None if total_suppressed else sum(
            1 for g in rows if g.get("review_status") == "completed"
        ),
        "suppressed": total_suppressed,
        "privacy_k": k,
        "metric_version": METRIC_VERSION,
        "note": (
            "Garment attributes extracted from GCS images (Vertex AI), attributed to the order PIN "
            "(fallback: customer home PIN). Includes unreviewed extractions; brand share uses reviewed only."
        ),
    }
