"""Server-side Geo Intelligence analysis (mirrors web/geo.html store scoring).

Works on the aggregated metrics payload only (customer_cells, garment_cells, pin_geo);
output never contains customer-level rows, coordinates, or competitor brand names.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

STORE_W = {"demand": 0.45, "garment": 0.25, "catchment": 0.20, "revenue": 0.10}
STORE_YES = 0.66
STORE_CONSIDER = 0.33


def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


def _clean_list(values: Optional[Sequence[Any]], limit: int = 20) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v or "").strip()][:limit]


def _matches(cell: dict[str, Any], *, ages: list[str], genders: list[str], rfms: list[str]) -> bool:
    if ages and _norm(cell.get("age_group")) not in {_norm(a) for a in ages}:
        return False
    if genders:
        cg = _norm(cell.get("gender"))[:1]
        if cg and cg not in {_norm(g)[:1] for g in genders}:
            return False
    if rfms and _norm(cell.get("rfm_segment")) not in {_norm(r) for r in rfms}:
        return False
    return True


def _norm01(values: list[float]):
    nums = [v for v in values if v is not None]
    if not nums:
        return lambda v: 0.0
    lo, hi = min(nums), max(nums)
    if hi <= lo:
        return lambda v: 1.0 if hi > 0 else 0.0
    return lambda v: (float(v) - lo) / (hi - lo)


def decision_from_score(
    score: float, demand: float, est_footfall: float, brand_share: Optional[float], brand_garments: float
) -> str:
    if not demand or demand <= 0:
        return "No"
    if not brand_garments or brand_garments <= 0:
        return "No"
    if est_footfall is not None and est_footfall < 40:
        return "No"
    if brand_share is not None:
        if brand_share >= 0.35 and est_footfall >= 60 and score >= STORE_CONSIDER:
            return "Yes"
        if brand_share >= 0.22 or (brand_garments >= 3 and score >= STORE_CONSIDER):
            return "Consider"
        return "No"
    if score >= STORE_YES:
        return "Yes"
    if score >= STORE_CONSIDER:
        return "Consider"
    return "No"


def _top(counter: dict[str, float], n: int = 5) -> list[dict[str, Any]]:
    return [
        {"value": k, "count": round(v, 2)}
        for k, v in sorted(counter.items(), key=lambda kv: -kv[1])[:n]
    ]


def resolve_analysis_brand(
    garments: list[dict[str, Any]], *, role: str, session_brand: Optional[str], requested: Optional[str]
) -> Optional[str]:
    if role == "brand":
        return (session_brand or "").strip() or None
    req = (requested or "").strip()
    if req and req.lower() != "competitors":
        return req
    counts: dict[str, float] = {}
    for g in garments:
        b = str(g.get("brand") or "").strip()
        if b and b != "Competitors":
            counts[b] = counts.get(b, 0) + float(g.get("garments") or 0)
    return max(counts, key=counts.get) if counts else None


def analyze(
    metrics: dict[str, Any],
    *,
    role: str,
    session_brand: Optional[str],
    pins: Optional[Sequence[str]] = None,
    ages: Optional[Sequence[str]] = None,
    genders: Optional[Sequence[str]] = None,
    rfms: Optional[Sequence[str]] = None,
    analysis_brand: Optional[str] = None,
    top_n: int = 8,
) -> dict[str, Any]:
    pin_f = _clean_list(pins, 50)
    age_f = _clean_list(ages)
    gen_f = _clean_list(genders, 4)
    rfm_f = _clean_list(rfms)
    pin_set = set(pin_f)

    customers = [c for c in (metrics.get("customer_cells") or []) if isinstance(c, dict)]
    garments = [g for g in (metrics.get("garment_cells") or []) if isinstance(g, dict)]
    pin_geo = metrics.get("pin_geo") if isinstance(metrics.get("pin_geo"), dict) else {}

    cust = [c for c in customers if _matches(c, ages=age_f, genders=gen_f, rfms=rfm_f)]
    garm = [g for g in garments if _matches(g, ages=age_f, genders=gen_f, rfms=rfm_f)]
    brand = resolve_analysis_brand(garm, role=role, session_brand=session_brand, requested=analysis_brand)
    own = _norm(brand)

    by_pin: dict[str, dict[str, Any]] = {}

    def row_for(pin: str) -> dict[str, Any]:
        return by_pin.setdefault(
            pin,
            {"demand": 0.0, "orders": 0.0, "revenue": 0.0, "brand_garments": 0.0,
             "competitor_garments": 0.0, "commodities": {}},
        )

    segments: dict[str, dict[str, float]] = {"rfm_segment": {}, "age_group": {}, "gender": {}}
    for c in cust:
        pin = str(c.get("pin_code") or "")
        if not pin or (pin_set and pin not in pin_set):
            continue
        r = row_for(pin)
        n = float(c.get("customers") or 0)
        r["demand"] += n
        r["orders"] += float(c.get("orders") or 0)
        r["revenue"] += float(c.get("revenue") or 0)
        for key in segments:
            val = str(c.get(key) or "").strip()
            if val:
                segments[key][val] = segments[key].get(val, 0) + n

    colors: dict[str, float] = {}
    own_commodities: dict[str, float] = {}
    for g in garm:
        pin = str(g.get("pin_code") or "")
        if not pin or (pin_set and pin not in pin_set):
            continue
        r = row_for(pin)
        n = float(g.get("garments") or 0)
        if own and _norm(g.get("brand")) == own:
            r["brand_garments"] += n
            for item in g.get("names") or []:
                name = (item or {}).get("value") or (item or {}).get("name")
                if name:
                    cnt = float((item or {}).get("count") or 1)
                    r["commodities"][name] = r["commodities"].get(name, 0) + cnt
                    own_commodities[name] = own_commodities.get(name, 0) + cnt
        else:
            r["competitor_garments"] += n
        for item in g.get("colors") or []:
            val = (item or {}).get("value")
            if val:
                colors[val] = colors.get(val, 0) + float((item or {}).get("count") or 1)

    rows: list[dict[str, Any]] = []
    for pin in (pin_f or list(pin_geo.keys())):
        geo = pin_geo.get(pin) or {}
        agg = by_pin.get(pin) or row_for(pin)
        catchment = float(geo.get("pop_buffer_2km") or geo.get("population_pin") or 0)
        demand = agg["demand"]
        est_footfall = round(demand * 18 + (catchment * 0.012 if demand > 0 else 0))
        wardrobe = agg["brand_garments"] + agg["competitor_garments"]
        share = (agg["brand_garments"] / wardrobe) if wardrobe > 0 else None
        rows.append(
            {
                "pin": pin,
                "area_label": geo.get("area_label") or pin,
                "demand": demand,
                "orders": agg["orders"],
                "revenue": round(agg["revenue"], 2),
                "garment_pull": wardrobe,
                "brand_garments": agg["brand_garments"],
                "competitor_garments": agg["competitor_garments"],
                "brand_share": round(share, 4) if share is not None else None,
                "catchment_pop": catchment,
                "revenue_intensity": (agg["revenue"] / demand) if demand > 0 else 0.0,
                "est_footfall": est_footfall,
                "commodities": [
                    {"commodity": k, "count": v}
                    for k, v in sorted(agg["commodities"].items(), key=lambda kv: -kv[1])[:8]
                ],
            }
        )

    nd = _norm01([r["demand"] for r in rows])
    ng = _norm01([r["brand_garments"] for r in rows])
    nc = _norm01([r["catchment_pop"] for r in rows])
    nr = _norm01([r["revenue_intensity"] for r in rows])
    ns = _norm01([r["brand_share"] or 0 for r in rows])
    for r in rows:
        catch = STORE_W["catchment"] * nc(r["catchment_pop"]) if r["demand"] > 0 else 0.0
        score = (
            STORE_W["demand"] * nd(r["demand"])
            + STORE_W["garment"] * ng(r["brand_garments"])
            + catch
            + STORE_W["revenue"] * nr(r["revenue_intensity"])
            + 0.15 * ns(r["brand_share"] or 0)
        )
        r["score"] = round(score, 3)
        r["heuristic_decision"] = decision_from_score(
            score, r["demand"], r["est_footfall"], r["brand_share"], r["brand_garments"]
        )
        r.pop("revenue_intensity", None)
    rows.sort(key=lambda r: (-r["score"], -r["demand"], -(r["brand_share"] or 0)))

    total_g = sum(r["garment_pull"] for r in rows)
    total_own = sum(r["brand_garments"] for r in rows)
    return {
        "analysis_brand": brand if role != "brand" else session_brand,
        "filters": {"pins": pin_f, "age": age_f, "gender": gen_f, "rfm": rfm_f},
        "kpis": {
            "customers": sum(r["demand"] for r in rows),
            "orders": sum(r["orders"] for r in rows),
            "revenue": round(sum(r["revenue"] for r in rows), 2),
            "garments": total_g,
        },
        "brand_share_overall": round(total_own / total_g, 4) if total_g else None,
        "pins": rows[:top_n],
        "pin_count": len(rows),
        "top_segments": {k: _top(v, 4) for k, v in segments.items()},
        "top_colors": _top(colors, 5),
        "top_brand_commodities": _top(own_commodities, 5),
        "notes": "Aggregated, PII-free view. Other brands are combined as Competitors.",
    }


def brief_payload(analysis: dict[str, Any], *, focus_pin: Optional[str], role: str) -> dict[str, Any]:
    """Build a GeoBriefRequest-shaped payload from an analysis result."""
    rows = analysis.get("pins") or []
    focus = None
    if focus_pin:
        focus = next((r for r in rows if r.get("pin") == focus_pin), None)
    if focus is None and rows:
        focus = rows[0]
    top_brand_commodity = (analysis.get("top_brand_commodities") or [{}])[0].get("value")
    top_color = (analysis.get("top_colors") or [{}])[0].get("value")
    return {
        "filters": analysis.get("filters") or {},
        "kpis": analysis.get("kpis") or {},
        "focus_pin": focus,
        "top_pins": rows[:8],
        "trends": {k: v for k, v in {"top_commodity": top_brand_commodity, "top_color": top_color}.items() if v},
        "analysis_brand": analysis.get("analysis_brand"),
        "role": role,
    }
