"""Privacy-safe customer demographic aggregates for B2B dashboards."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from suffai import METRIC_VERSION, PRIVACY_K
from suffai.privacy.suppress import suppress_cell


def age_band(age: Optional[int]) -> str:
    if age is None:
        return "unknown"
    try:
        a = int(age)
    except (TypeError, ValueError):
        return "unknown"
    if a < 18:
        return "under_18"
    if a <= 24:
        return "18_24"
    if a <= 34:
        return "25_34"
    if a <= 44:
        return "35_44"
    if a <= 54:
        return "45_54"
    return "55_plus"


def filter_customer_ids(
    customers_by_id: dict[str, dict[str, Any]],
    customer_ids: Iterable[str],
    *,
    genders: list[str] | None = None,
    age_bands: list[str] | None = None,
    device_types: list[str] | None = None,
    pins: list[str] | None = None,
) -> set[str]:
    """Return customer_uuids matching demographic filters."""
    gender_set = {g.lower() for g in (genders or []) if g}
    band_set = {b.lower() for b in (age_bands or []) if b}
    device_set = {d.lower() for d in (device_types or []) if d}
    pin_set = {p for p in (pins or []) if p}

    out: set[str] = set()
    for cid in customer_ids:
        if not cid:
            continue
        row = customers_by_id.get(cid)
        if not row:
            # Keep unknown profiles only when no demographic filters applied
            if not (gender_set or band_set or device_set or pin_set):
                out.add(cid)
            continue
        if gender_set and str(row.get("gender") or "unknown").lower() not in gender_set:
            continue
        if band_set and age_band(row.get("age")).lower() not in band_set:
            continue
        if device_set and str(row.get("device_type") or "unknown").lower() not in device_set:
            continue
        if pin_set and (row.get("pin") or "UNKNOWN") not in pin_set and "UNKNOWN" not in pin_set:
            continue
        out.add(cid)
    return out


def _mix(
    rows: list[dict[str, Any]],
    field: str,
    *,
    k: int = PRIVACY_K,
    transform=None,
) -> list[dict[str, Any]]:
    counts: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        cid = r.get("customer_uuid") or r.get("customer_id")
        if not cid:
            continue
        raw = r.get(field)
        if transform:
            val = transform(raw)
        else:
            val = str(raw or "unknown").lower()
        counts[str(val)].add(cid)

    out = []
    for value, cids in sorted(counts.items(), key=lambda x: -len(x[1])):
        n = len(cids)
        cell = suppress_cell(n, n, k=k)
        out.append({
            "value": value,
            "customers": None if cell["suppressed"] else n,
            "suppressed": cell["suppressed"],
        })
    return out


def demographic_summary(
    customers_by_id: dict[str, dict[str, Any]],
    customer_ids: Iterable[str],
    *,
    k: int = PRIVACY_K,
) -> dict[str, Any]:
    """Aggregate gender / age_band / device / pin for a cohort (no raw PII)."""
    rows = []
    missing = 0
    for cid in set(customer_ids):
        if not cid:
            continue
        row = customers_by_id.get(cid)
        if not row:
            missing += 1
            continue
        rows.append(row)

    matched = len(rows)
    suppressed_total = matched < k
    return {
        "cohort_customers": None if suppressed_total else matched,
        "profiles_missing": missing,
        "suppressed": suppressed_total,
        "gender_mix": [] if suppressed_total else _mix(rows, "gender", k=k),
        "age_band_mix": [] if suppressed_total else _mix(
            rows, "age", k=k, transform=age_band
        ),
        "device_mix": [] if suppressed_total else _mix(rows, "device_type", k=k),
        "pin_mix": [] if suppressed_total else _mix(rows, "pin", k=k)[:20],
        "metric_version": METRIC_VERSION,
        "privacy_k": k,
        "note": (
            "Aggregates only — phone_number and address are never returned to brand dashboards."
        ),
    }


def enrich_garments_with_customers(
    garments: list[dict[str, Any]],
    customers_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach customer demographics (non-PII) onto garment rows for analytics."""
    out = []
    for g in garments:
        cid = g.get("source_customer_id") or g.get("user_uuid")
        cust = customers_by_id.get(cid) if cid else None
        row = dict(g)
        if cust:
            row["customer_gender"] = cust.get("gender")
            row["customer_age"] = cust.get("age")
            row["customer_age_band"] = age_band(cust.get("age"))
            row["customer_pin"] = cust.get("pin")
            row["customer_device_type"] = cust.get("device_type")
        else:
            # Keep values already joined upstream (e.g. BigQuery v_garment_enriched)
            row.setdefault("customer_gender", None)
            row.setdefault("customer_age", None)
            row["customer_age_band"] = row.get("customer_age_band") or "unknown"
            row.setdefault("customer_pin", None)
            row.setdefault("customer_device_type", None)
        out.append(row)
    return out
