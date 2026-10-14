"""Spatial command center: per-PIN hub profiles for the Geo map (k-suppressed).

Everything here is derived from orders, stores, complaints, customer ~1 km grid cells and
extracted garments. Store markers sit at their PIN centroid (stores carry no coordinates),
and catchment rings are straight-line radii, not drive-time isochrones.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Iterable, Optional

from suffai import METRIC_VERSION, PRIVACY_K
from suffai.ingest.normalize import geo_cell
from suffai.metrics.eligibility import is_eligible_order
from suffai.metrics.geo import PIN_AREA_LABELS

CATCHMENT_KM = (1.0, 2.2, 3.8)
HEAT_CELL_DEGREES = 0.02
SIZE_ORDER = ("XS", "S", "M", "L", "XL", "XXL", "3XL")
CANCEL_STATUSES = {"cancel", "cancelled", "canceled"}
LIVE_GARMENT_STATUSES = {"completed", "needs_review"}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _customer_cell(c: dict[str, Any]) -> Optional[tuple[float, float]]:
    lat, lng = c.get("home_lat_cell"), c.get("home_lng_cell")
    if lat is None or lng is None:
        lat, lng = geo_cell(c.get("address"))
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


def _polygon_index(polygons: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for f in polygons.get("features") or []:
        props = f.get("properties") or {}
        pin = str(props.get("pin_code") or "")
        if not pin:
            continue
        ring = ((f.get("geometry") or {}).get("coordinates") or [[]])[0]
        lat, lon = props.get("lat"), props.get("lon")
        if (lat is None or lon is None) and ring:
            lon = sum(p[0] for p in ring) / len(ring)
            lat = sum(p[1] for p in ring) / len(ring)
        out[pin] = {"lat": lat, "lon": lon, "ring": [[p[1], p[0]] for p in ring], "area": props.get("area")}
    return out


def _top(counter: Counter, n: int, total: Optional[int] = None) -> list[dict[str, Any]]:
    total = total if total is not None else sum(counter.values())
    return [
        {"value": v, "count": c, "share": round(c / total, 4) if total else None}
        for v, c in counter.most_common()
        if v and v != "unknown"
    ][:n]


def _size_key(size: str) -> tuple[int, str]:
    s = size.upper()
    return (SIZE_ORDER.index(s), s) if s in SIZE_ORDER else (len(SIZE_ORDER), s)


def garment_profile(items: list[dict[str, Any]], *, my_brand: str, k: int) -> dict[str, Any]:
    """Attribute mixes for a set of garments; empty unless >= k distinct customers."""
    customers = {g.get("source_customer_id") or g.get("user_uuid") for g in items} - {None}
    if len(customers) < k:
        return {"garments": None, "customers": None, "suppressed": True}
    sizes = Counter(str(g.get("size_normalized") or g.get("size_raw") or "unknown").upper() for g in items)
    sized = sum(c for s, c in sizes.items() if s != "UNKNOWN")
    size_curve = [
        {"size": s, "count": c, "share": round(c / sized, 4)}
        for s, c in sorted(sizes.items(), key=lambda kv: _size_key(kv[0]))
        if s != "UNKNOWN" and sized
    ]
    styles: Counter = Counter()
    for g in items:
        for tag in g.get("style_tags") or []:
            styles[str(tag).lower()] += 1

    reviewed = [g for g in items if g.get("review_status") == "completed"]
    reviewed_customers = {g.get("source_customer_id") or g.get("user_uuid") for g in reviewed} - {None}
    brand_mix: list[dict[str, Any]] = []
    competitor_share = None
    if len(reviewed_customers) >= k:
        brands = Counter(str(g.get("brand_name_raw") or "unknown") for g in reviewed)
        branded = sum(c for b, c in brands.items() if b.lower() != "unknown")
        own = sum(c for b, c in brands.items() if my_brand and b.lower() == my_brand.lower())
        brand_mix = [
            {"brand": b, "garments": c, "share": round(c / branded, 4), "is_own": bool(my_brand) and b.lower() == my_brand.lower()}
            for b, c in brands.most_common(8)
            if b.lower() != "unknown" and branded
        ]
        competitor_share = round(1 - own / branded, 4) if branded else None

    return {
        "garments": len(items),
        "customers": len(customers),
        "suppressed": False,
        "size_curve": size_curve,
        "materials": _top(Counter(str(g.get("material_type") or "unknown").lower() for g in items), 3),
        "colors": _top(Counter(str(g.get("primary_color") or "unknown").lower() for g in items), 4),
        "styles": _top(styles, 3, total=len(items)),
        "commodity_types": _top(Counter(str(g.get("commodity_type") or "unknown").lower() for g in items), 3),
        "patterns": _top(Counter(str(g.get("pattern") or "unknown").lower() for g in items), 3),
        "brand_mix": brand_mix,
        "competitor_share": competitor_share,
    }


def _window_months(orders: Iterable[dict[str, Any]]) -> tuple[float, Optional[str], Optional[str]]:
    stamps = [o["ordered_at"] for o in orders if o.get("ordered_at") is not None]
    if not stamps:
        return 1.0, None, None
    lo, hi = min(stamps), max(stamps)
    months = max(1.0, (hi - lo).days / 30.44)
    return months, lo.isoformat(), hi.isoformat()


def _store_offset(store_id: str) -> tuple[float, float]:
    h = int(hashlib.sha1(store_id.encode()).hexdigest()[:8], 16)
    angle = (h % 360) * math.pi / 180
    radius = 0.002 + (h % 7) * 0.0006
    return radius * math.sin(angle), radius * math.cos(angle)


def command_center(
    *,
    orders: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    stores: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    complaints: list[dict[str, Any]],
    garments: list[dict[str, Any]],
    polygons: dict[str, Any],
    my_brand: str = "",
    k: int = PRIVACY_K,
) -> dict[str, Any]:
    poly = _polygon_index(polygons)
    cells: dict[str, tuple[float, float]] = {}
    for c in customers:
        cid = c.get("customer_id") or c.get("customer_uuid")
        cell = _customer_cell(c)
        if cid and cell:
            cells[str(cid)] = cell

    eligible = [o for o in orders if is_eligible_order(o)]
    months, window_from, window_to = _window_months(eligible)
    store_by_id = {s["store_id"]: s for s in stores if s.get("store_id")}
    order_pin = {o["order_id"]: o.get("order_pin") for o in orders if o.get("order_id")}

    orders_by_pin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in orders:
        if o.get("order_pin"):
            orders_by_pin[o["order_pin"]].append(o)
    complaints_by_pin: Counter = Counter(
        order_pin.get(c.get("order_id")) for c in complaints if order_pin.get(c.get("order_id"))
    )
    lines_by_pin: dict[str, Counter] = defaultdict(Counter)
    eligible_ids = {o["order_id"] for o in eligible}
    for ln in lines:
        if ln.get("order_id") in eligible_ids and ln.get("order_pin"):
            lines_by_pin[ln["order_pin"]][ln.get("commodity_name") or "unknown"] += int(ln.get("quantity") or 0)
    garments_by_pin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    live_garments = [
        g for g in garments if not g.get("deleted_at") and g.get("review_status") in LIVE_GARMENT_STATUSES
    ]
    for g in live_garments:
        if g.get("order_pin_code"):
            garments_by_pin[g["order_pin_code"]].append(g)

    # Centroids: licensed/demo polygons first, else median grid cell of >= k customers ordering there.
    centroids: dict[str, tuple[float, float]] = {
        pin: (p["lat"], p["lon"]) for pin, p in poly.items() if p["lat"] is not None
    }
    for pin, pin_orders in orders_by_pin.items():
        if pin in centroids:
            continue
        pts = {o.get("customer_id"): cells[o["customer_id"]] for o in pin_orders if o.get("customer_id") in cells}
        if len(pts) >= k:
            centroids[pin] = (median(p[0] for p in pts.values()), median(p[1] for p in pts.values()))

    store_points = {}
    for s in stores:
        c = centroids.get(s.get("pin_code") or "")
        if c:
            dlat, dlon = _store_offset(s["store_id"])
            store_points[s["store_id"]] = (c[0] + dlat, c[1] + dlon)

    store_orders = Counter(o.get("store_id") for o in eligible if o.get("store_id"))
    store_rows = [
        {
            "store_id": sid,
            "store_name": store_by_id[sid].get("store_name"),
            "area": store_by_id[sid].get("area"),
            "pin_code": store_by_id[sid].get("pin_code"),
            "status": store_by_id[sid].get("status"),
            "premium": bool(store_by_id[sid].get("premium_laundry")),
            "lat": round(pt[0], 5),
            "lon": round(pt[1], 5),
            "orders_per_day": round(store_orders.get(sid, 0) / (months * 30.44), 2),
            "location_precision": "pin_centroid",
        }
        for sid, pt in store_points.items()
    ]

    hubs = []
    for pin, pin_orders in orders_by_pin.items():
        elig = [o for o in pin_orders if o["order_id"] in eligible_ids]
        active = {o.get("customer_id") for o in elig} - {None}
        centroid = centroids.get(pin)
        base = {
            "pin_code": pin,
            "area": PIN_AREA_LABELS.get(pin) or (poly.get(pin) or {}).get("area") or pin,
            "lat": centroid[0] if centroid else None,
            "lon": centroid[1] if centroid else None,
            "polygon": (poly.get(pin) or {}).get("ring"),
            "location_precision": "polygon" if pin in poly else ("customer_cells" if centroid else None),
        }
        if len(active) < k:
            hubs.append({**base, "suppressed": True})
            continue

        revenue = sum(float(o.get("total_amount") or 0) for o in elig)
        per_customer = Counter(o.get("customer_id") for o in elig)
        local = sum(1 for o in elig if (store_by_id.get(o.get("store_id")) or {}).get("pin_code") == pin)
        cancelled = sum(1 for o in pin_orders if (o.get("order_status") or "").lower() in CANCEL_STATUSES)

        nearest = None
        catchment = None
        if centroid:
            best = None
            for sid, pt in store_points.items():
                d = haversine_km(centroid, pt)
                if best is None or d < best[0]:
                    best = (d, sid)
            if best:
                s = store_by_id[best[1]]
                nearest = {
                    "store_name": s.get("store_name"),
                    "area": s.get("area"),
                    "pin_code": s.get("pin_code"),
                    "km": round(best[0], 1),
                }
            catchment = []
            for radius in CATCHMENT_KM:
                n = sum(1 for pt in cells.values() if haversine_km(centroid, pt) <= radius)
                catchment.append({"radius_km": radius, "customers": n if n >= k else None, "suppressed": n < k})

        hubs.append({
            **base,
            "suppressed": False,
            "active_customers": len(active),
            "orders_per_month": round(len(elig) / months, 1),
            "revenue_per_month": round(revenue / months, 2),
            "avg_order_value": round(revenue / len(elig), 2) if elig else None,
            "repeat_rate": round(sum(1 for n in per_customer.values() if n >= 2) / len(active), 4),
            "served_local_pct": round(local / len(elig), 4) if elig else None,
            "stores_in_pin": sum(1 for s in stores if s.get("pin_code") == pin),
            "nearest_store": nearest,
            "catchment": catchment,
            "cancel_rate": round(cancelled / len(pin_orders), 4),
            "complaint_rate": round(complaints_by_pin.get(pin, 0) / len(pin_orders), 4),
            "complaints": complaints_by_pin.get(pin, 0),
            "orders_total": len(pin_orders),
            "commodities": _top(lines_by_pin.get(pin, Counter()), 5),
            "garments": garment_profile(garments_by_pin.get(pin, []), my_brand=my_brand, k=k),
        })

    ranked = sorted((h for h in hubs if not h["suppressed"]), key=lambda h: -h["orders_per_month"])
    for i, h in enumerate(ranked):
        pct = i / max(1, len(ranked))
        h["rank"] = i + 1
        h["grade"] = "A+" if pct < 0.1 else "A" if pct < 0.3 else "B" if pct < 0.6 else "C"
    hubs.sort(key=lambda h: (h["suppressed"], h.get("rank") or 10**6))

    heat_counts: dict[tuple[float, float], set[str]] = defaultdict(set)
    for cid, (lat, lng) in cells.items():
        key = (
            round(math.floor(lat / HEAT_CELL_DEGREES) * HEAT_CELL_DEGREES, 4),
            round(math.floor(lng / HEAT_CELL_DEGREES) * HEAT_CELL_DEGREES, 4),
        )
        heat_counts[key].add(cid)
    heat = [
        {"lat": lat, "lon": lng, "size_deg": HEAT_CELL_DEGREES, "customers": len(ids)}
        for (lat, lng), ids in heat_counts.items()
        if len(ids) >= k
    ]

    overall = garment_profile(live_garments, my_brand=my_brand, k=k)
    mapped = [h for h in ranked if h["lat"] is not None]
    return {
        "kpis": {
            "hubs": len(mapped),
            "orders_per_month": round(sum(h["orders_per_month"] for h in ranked), 1),
            "revenue_per_month": round(sum(h["revenue_per_month"] for h in ranked), 2),
            "competitor_share": overall.get("competitor_share"),
            "served_local_pct": (
                round(sum(h["served_local_pct"] or 0 for h in ranked) / len(ranked), 4) if ranked else None
            ),
        },
        "hubs": hubs,
        "stores": store_rows,
        "heat": heat,
        "garments_overall": overall,
        "window": {"from": window_from, "to": window_to, "months": round(months, 1)},
        "privacy_k": k,
        "metric_version": METRIC_VERSION,
        "notes": {
            "stores": "Store markers sit at their PIN centroid — stores carry no coordinates.",
            "catchment": "Rings are straight-line radii (1 / 2.2 / 3.8 km), counting customers by ~1 km home grid cell.",
            "heat": f"Customer density per ~2 km cell from geo-tagged addresses; cells under k={k} hidden.",
            "garments": "Garment mixes come from Vertex extractions attributed to the order PIN; brand mix uses reviewed garments only.",
            "returns": "Source orders carry no refunds; cancellations and complaints stand in for return friction.",
        },
    }
