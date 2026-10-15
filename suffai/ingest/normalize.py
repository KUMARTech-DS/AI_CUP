"""Normalize source documents into flat facts. Preserves nested line IDs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from suffai.ids import extract_datetime, extract_id, normalize_pin


def _format_address(doc: dict[str, Any]) -> str | None:
    saved = doc.get("saved_addresses") or []
    if isinstance(saved, list) and saved:
        addr = saved[0]
        if isinstance(addr, dict):
            parts = [
                addr.get("street"),
                addr.get("city"),
                addr.get("state"),
                addr.get("postalCode") or addr.get("pin_code"),
                addr.get("country"),
            ]
            text = ", ".join(str(p).strip() for p in parts if p and str(p).strip())
            if text:
                return text
    loc = doc.get("location")
    if isinstance(loc, dict) and loc.get("coordinates"):
        coords = loc["coordinates"]
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            return f"geo:{coords[1]},{coords[0]}"
    return None


GEO_CELL_DEGREES = 0.01  # ~1.1 km; coarse enough to never locate a single household


def geo_cell(address: Any) -> tuple[float | None, float | None]:
    """Snap a `geo:lat,lng` address to a ~1 km grid cell (privacy-safe heatmap key)."""
    text = str(address or "")
    if not text.startswith("geo:"):
        return None, None
    try:
        lat_s, lng_s = text[4:].split(",", 1)
        lat, lng = float(lat_s), float(lng_s)
    except ValueError:
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return None, None
    snap = lambda v: round(round(v / GEO_CELL_DEGREES) * GEO_CELL_DEGREES, 4)  # noqa: E731
    return snap(lat), snap(lng)


def _age_from_dob(dob: datetime | None) -> int | None:
    if dob is None:
        return None
    today = datetime.now(timezone.utc).date()
    d = dob.date() if hasattr(dob, "date") else dob
    try:
        years = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    except Exception:
        return None
    if years < 0 or years > 120:
        return None
    return years


def _device_type(doc: dict[str, Any]) -> str | None:
    raw = doc.get("customerDeviceAppType") or doc.get("device_type") or doc.get("deviceType")
    if not raw:
        return None
    text = str(raw).strip()
    mapping = {
        "mobileapptype": "mobile",
        "websiteapptype": "web",
        "mobile": "mobile",
        "web": "web",
        "ios": "ios",
        "android": "android",
    }
    return mapping.get(text.lower(), text.lower())


def normalize_customers(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for doc in raw:
        customer_id = extract_id(doc.get("_id"))
        dob = extract_datetime(doc.get("dob"))
        pin = normalize_pin(doc.get("pin_code"))
        if not pin:
            saved = doc.get("saved_addresses") or []
            if isinstance(saved, list) and saved and isinstance(saved[0], dict):
                pin = normalize_pin(saved[0].get("postalCode") or saved[0].get("pin_code"))
        gender = doc.get("gender")
        if isinstance(gender, str):
            gender = gender.strip().lower() or None
        address = _format_address(doc)
        lat_cell, lng_cell = geo_cell(address)
        rows.append({
            "customer_id": customer_id,
            # Alias used by garment image naming / extraction user_uuid
            "customer_uuid": customer_id,
            "user_id": extract_id(doc.get("user_id")),
            "customer_name": doc.get("customer_name"),
            "phone_no": doc.get("phone_no"),
            "phone_number": doc.get("phone_no"),
            "email_address": doc.get("email_address"),
            "gender": gender,
            "dob": dob,
            "age": _age_from_dob(dob),
            "home_pin": pin,
            "pin": pin,
            "address": address,
            "home_lat_cell": lat_cell,
            "home_lng_cell": lng_cell,
            "device_type": _device_type(doc),
            "created_at": extract_datetime(doc.get("created_at")),
            "updated_at": extract_datetime(doc.get("updated_at")),
        })
    return [r for r in rows if r["customer_id"]]


def normalize_stores(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for doc in raw:
        rows.append({
            "store_id": extract_id(doc.get("_id")),
            "store_name": doc.get("store_name"),
            "pin_code": normalize_pin(doc.get("pin_code")),
            "city": doc.get("city"),
            "area": doc.get("area"),
            "premium_laundry": bool(doc.get("premium_laundry")),
            "store_category": doc.get("suffai_store_base_category"),
            "status": doc.get("status"),
        })
    return [r for r in rows if r["store_id"]]


def normalize_services(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "service_id": extract_id(doc.get("_id")),
            "service_name": doc.get("service_name"),
        }
        for doc in raw
        if extract_id(doc.get("_id"))
    ]


def normalize_commodities(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "catalog_commodity_id": extract_id(doc.get("_id")),
            "name": doc.get("name"),
            "menu": doc.get("menu"),
            "service_id": extract_id(doc.get("service_id")),
            "image_url": doc.get("image_url"),  # catalog imagery, NOT customer garment
        }
        for doc in raw
        if extract_id(doc.get("_id"))
    ]


def _order_pin(doc: dict[str, Any]) -> str | None:
    sch = doc.get("schedule")
    if isinstance(sch, dict):
        addr = sch.get("address")
        if isinstance(addr, dict):
            return normalize_pin(addr.get("pin_code"))
    return None


def normalize_orders(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for doc in raw:
        oid = extract_id(doc.get("_id"))
        if not oid:
            continue
        bill = doc.get("bill_number")
        if bill is not None:
            bill = str(bill).strip() or None
        rows.append({
            "order_id": oid,
            "customer_id": extract_id(doc.get("customer_id")),
            "store_id": extract_id(doc.get("store_id")),
            "bill_no": bill,
            "order_status": str(doc.get("order_status") or "").strip() or None,
            "payment_status": str(doc.get("payment_status") or "").strip() or None,
            "refund_flag": bool(doc.get("refund_flag")),
            "total_amount": _num(doc.get("total_amount")),
            "sub_total_amount": _num(doc.get("sub_total_amount")),
            "ordered_at": extract_datetime(doc.get("order_date_time") or doc.get("created_at")),
            "order_pin": _order_pin(doc),
            "total_number_of_clothes": _int(doc.get("total_number_of_clothes")),
        })
    return rows


def normalize_order_commodity_lines(raw_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """UNNEST orderItems × orderedAccommodities. Quantity lines ≠ garment instances."""
    lines: list[dict[str, Any]] = []
    for doc in raw_orders:
        order_id = extract_id(doc.get("_id"))
        if not order_id:
            continue
        customer_id = extract_id(doc.get("customer_id"))
        store_id = extract_id(doc.get("store_id"))
        order_pin = _order_pin(doc)
        ordered_at = extract_datetime(doc.get("order_date_time") or doc.get("created_at"))
        items = doc.get("orderItems") or []
        if not isinstance(items, list):
            continue
        for itm in items:
            if not isinstance(itm, dict):
                continue
            order_item_id = extract_id(itm.get("_id"))
            service_id = extract_id(itm.get("service_id"))
            service_name = itm.get("name")
            accs = itm.get("orderedAccommodities") or []
            if not isinstance(accs, list) or not accs:
                continue
            for acc in accs:
                if not isinstance(acc, dict):
                    continue
                line_id = extract_id(acc.get("_id"))
                # Source spelling: accomodityId (missing 'm')
                catalog_id = extract_id(acc.get("accomodityId") or acc.get("commodityId"))
                qty = _int(acc.get("quantity")) or 0
                lines.append({
                    "line_id": line_id,
                    "order_id": order_id,
                    "order_item_id": order_item_id,
                    "customer_id": customer_id,
                    "store_id": store_id,
                    "service_id": service_id,
                    "service_name": service_name,
                    "catalog_commodity_id": catalog_id,
                    "commodity_name": acc.get("commodityName") or acc.get("name"),
                    "menu": acc.get("menu"),
                    "quantity": qty,
                    "line_price": _num(acc.get("price")),
                    "order_pin": order_pin,
                    "ordered_at": ordered_at,
                })
    return lines


def normalize_complaints(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for doc in raw:
        cid = extract_id(doc.get("_id"))
        if not cid:
            continue
        rows.append({
            "complaint_id": cid,
            "order_id": extract_id(doc.get("order_id")),
            "customer_id": extract_id(doc.get("customer_id")),
            "store_id": extract_id(doc.get("store_id")),
            "complaint_status": doc.get("complaint_status"),
            "resolved": bool(doc.get("resolved")),
            "created_at": extract_datetime(doc.get("created_at")),
        })
    return rows


def _num(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def normalize_all(raw: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "customers": normalize_customers(raw.get("customers", [])),
        "stores": normalize_stores(raw.get("stores", [])),
        "services": normalize_services(raw.get("services", [])),
        "commodities": normalize_commodities(raw.get("commodities", [])),
        "orders": normalize_orders(raw.get("orders", [])),
        "order_commodity_lines": normalize_order_commodity_lines(raw.get("orders", [])),
        "complaints": normalize_complaints(raw.get("complaintorders", [])),
    }
