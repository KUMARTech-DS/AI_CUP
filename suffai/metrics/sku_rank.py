"""SKU opportunity ranking (SUFFAI SKU Ranking PRD/ERD v1.2).

score = 0.4 visual relevance + 0.3 local adoption + 0.2 trend momentum + 0.1 seasonal relevance,
each on a fixed 0–100 calibration (never normalised within a batch), with missing factors
dropped and the remaining weights renormalised. Opportunity is a historical garment-demand
proxy for the PIN, not a sales forecast.

Evidence ladder: consented garment extractions in the PIN → service order lines in the PIN
(commodity family only) → market-wide garment extractions. Every customer-derived count is
suppressed below k distinct customers.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from suffai import PRIVACY_K
from suffai.metrics.command_center import LIVE_GARMENT_STATUSES, garment_profile
from suffai.metrics.demographics import age_band
from suffai.metrics.eligibility import is_eligible_order
from suffai.metrics.geo import PIN_AREA_LABELS
from suffai.metrics.research_lab import silhouette_for

SCORING_VERSION = "sku-rank-v1.2"
DEFAULT_LOOKBACK_DAYS = 180
WEIGHTS = {
    "visual_relevance": 0.40,
    "local_adoption": 0.30,
    "trend_momentum": 0.20,
    "seasonal_relevance": 0.10,
}
VISUAL_ATTRS = {
    "commodity": (0.40, 0.30),
    "color": (0.25, 0.20),
    "pattern": (0.15, 0.50),
    "gender": (0.10, 0.50),
    "style": (0.10, 0.25),
}
ADOPTION_PENETRATION_SATURATION = {"pin_garments": 0.20, "market_garments": 0.20, "pin_service_lines": 0.50}
ADOPTION_VOLUME_SATURATION = 500
UNKNOWN = {"", "unknown", "other", "none", "null", "n/a"}
SCOPE_LABELS = {
    "pin_garments": "garment extractions in this PIN",
    "pin_service_lines": "service order lines in this PIN (commodity family only)",
    "market_garments": "garment extractions across all PINs (PIN history below k)",
}

COMMODITY_ALIASES = {
    "t-shirt": "tshirt", "t shirt": "tshirt", "tee": "tshirt", "tees": "tshirt", "polo": "tshirt",
    "trousers": "trouser", "pants": "trouser", "pant": "trouser", "chinos": "trouser", "joggers": "trouser",
    "jean": "jeans", "denims": "jeans", "sweatshirt": "hoodie", "hoody": "hoodie",
    "sari": "saree", "kurti": "kurta", "shirts": "shirt", "jackets": "jacket",
}
COLOR_FAMILIES = {
    "dark": ("black", "charcoal", "navy", "navy blue", "dark grey"),
    "neutral": ("white", "off white", "cream", "ivory", "beige", "khaki", "grey", "gray", "silver", "sand"),
    "blue": ("blue", "light blue", "sky blue", "denim", "teal", "turquoise", "cyan"),
    "green": ("green", "olive", "olive green", "sage", "mint", "lime"),
    "warm": ("red", "maroon", "burgundy", "wine", "orange", "coral", "peach", "mustard", "yellow", "gold", "crimson"),
    "pink_purple": ("pink", "hot pink", "magenta", "purple", "violet", "lavender"),
    "brown": ("brown", "chocolate", "camel", "tan"),
}
_COLOR_TO_FAMILY = {c: fam for fam, names in COLOR_FAMILIES.items() for c in names}

SEASON_BY_MONTH = {12: "winter", 1: "winter", 2: "winter", 3: "summer", 4: "summer", 5: "summer",
                   6: "monsoon", 7: "monsoon", 8: "monsoon", 9: "monsoon", 10: "festive", 11: "festive"}
SEASON_PROFILES = {
    "winter_wear": {"winter": 100, "festive": 75, "monsoon": 55, "summer": 20},
    "festive_wear": {"festive": 100, "winter": 70, "summer": 55, "monsoon": 50},
    "summer_wear": {"summer": 100, "monsoon": 70, "festive": 60, "winter": 40},
}
SEASON_MATERIALS = {"wool": "winter_wear", "fleece": "winter_wear", "silk": "festive_wear", "linen": "summer_wear"}
SEASON_COMMODITIES = {
    "winter_wear": ("jacket", "hoodie", "sweater", "coat", "blazer", "pullover", "cardigan", "thermal"),
    "festive_wear": ("kurta", "saree", "lehenga", "sherwani", "ethnic"),
    "summer_wear": ("tshirt", "shorts", "tank"),
}
ALL_SEASON_SCORE = 65
RELIABLE_ATTRIBUTE_CONFIDENCE = 0.4


def _known(v: Any) -> bool:
    return str(v or "").strip().lower() not in UNKNOWN


def norm_commodity(v: Any) -> str:
    s = str(v or "").strip().lower().replace("_", " ")
    return COMMODITY_ALIASES.get(s, s)


def color_family(v: Any) -> Optional[str]:
    s = str(v or "").strip().lower()
    if s in _COLOR_TO_FAMILY:
        return _COLOR_TO_FAMILY[s]
    for w in reversed(s.replace("-", " ").split()):
        if w in _COLOR_TO_FAMILY:
            return _COLOR_TO_FAMILY[w]
    return None


def _ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def consent_filter(consent_records: Iterable[dict[str, Any]], mode: str = "opt_out") -> Callable[[Optional[str]], bool]:
    """Latest consent record per customer wins. opt_out: revoked excluded; opt_in: only granted kept."""
    latest: dict[str, tuple[str, str]] = {}
    for r in consent_records or []:
        cid = r.get("source_customer_id")
        if not cid:
            continue
        stamp = str(r.get("revoked_at") or r.get("granted_at") or r.get("created_at") or "")
        if cid not in latest or stamp >= latest[cid][0]:
            latest[cid] = (stamp, str(r.get("status") or "").lower())
    if mode == "opt_in":
        granted = {c for c, (_, s) in latest.items() if s == "granted"}
        return lambda cid: bool(cid) and cid in granted
    revoked = {c for c, (_, s) in latest.items() if s == "revoked"}
    return lambda cid: bool(cid) and cid not in revoked


@dataclass
class Obs:
    cid: str
    ts: Optional[datetime]
    family: str
    commodity: str = "unknown"
    color: str = "unknown"
    color_family: Optional[str] = None
    pattern: str = "unknown"
    gender: str = "unknown"
    styles: frozenset = frozenset()


@dataclass
class Pool:
    scope: str
    obs: list[Obs]
    as_of: Optional[datetime]
    lookback_days: int
    by_customer: dict[str, list[Obs]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        grouped: dict[str, list[Obs]] = defaultdict(list)
        for o in self.obs:
            grouped[o.cid].append(o)
        self.by_customer = dict(grouped)

    @property
    def customers(self) -> int:
        return len(self.by_customer)

    def window(self, recent: bool) -> tuple[Optional[datetime], Optional[datetime]]:
        if self.as_of is None:
            return None, None
        mid = self.as_of - timedelta(days=self.lookback_days / 2)
        return (mid, self.as_of) if recent else (self.as_of - timedelta(days=self.lookback_days), mid)


@dataclass
class RankContext:
    pincode: str
    lookback_days: int
    k: int
    pin_garments: Pool
    market_garments: Pool
    pin_lines: Pool
    customers_by_id: dict[str, dict[str, Any]]
    rfm_by_customer: dict[str, str]
    area_profile: dict[str, Any]

    @property
    def data_as_of(self) -> Optional[str]:
        stamps = [p.as_of for p in (self.pin_garments, self.market_garments, self.pin_lines) if p.as_of]
        return max(stamps).isoformat() if stamps else None


def _garment_obs(g: dict[str, Any]) -> Optional[Obs]:
    cid = g.get("source_customer_id") or g.get("user_uuid")
    if not cid:
        return None
    commodity = norm_commodity(g.get("commodity_type"))
    color = str(g.get("primary_color") or "unknown").strip().lower()
    return Obs(
        cid=str(cid),
        ts=_ts(g.get("observed_at") or g.get("updated_at") or g.get("created_at")),
        family=silhouette_for(commodity) if _known(commodity) else "generic",
        commodity=commodity if _known(commodity) else "unknown",
        color=color if _known(color) else "unknown",
        color_family=color_family(color),
        pattern=str(g.get("pattern") or "unknown").strip().lower(),
        gender=str(g.get("gender") or "unknown").strip().lower(),
        styles=frozenset(str(t).strip().lower() for t in g.get("style_tags") or [] if t),
    )


def _in_lookback(obs: list[Obs], lookback_days: int) -> tuple[list[Obs], Optional[datetime]]:
    stamps = [o.ts for o in obs if o.ts]
    if not stamps:
        return [], None
    as_of = max(stamps)
    start = as_of - timedelta(days=lookback_days)
    return [o for o in obs if o.ts and o.ts > start], as_of


def build_context(
    *,
    pincode: str,
    garments: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    customers_by_id: dict[str, dict[str, Any]],
    rfm_snapshots: list[dict[str, Any]],
    consent_records: list[dict[str, Any]],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    consent_mode: str = "opt_out",
    k: int = PRIVACY_K,
) -> RankContext:
    allowed = consent_filter(consent_records, consent_mode)

    live = [
        g for g in garments
        if not g.get("deleted_at") and str(g.get("review_status") or "").lower() in LIVE_GARMENT_STATUSES
    ]
    pairs = [
        (g, o) for g, o in ((g, _garment_obs(g)) for g in live)
        if o is not None and allowed(o.cid)
    ]
    market_window, market_as_of = _in_lookback([o for _, o in pairs], lookback_days)
    pin_pairs = [(g, o) for g, o in pairs if str(g.get("order_pin_code") or g.get("pin_code") or "") == pincode]
    pin_window, pin_as_of = _in_lookback([o for _, o in pin_pairs], lookback_days)
    in_window = {id(o) for o in pin_window}
    pin_garment_rows = [g for g, o in pin_pairs if id(o) in in_window]

    eligible_ids = {o["order_id"] for o in orders if o.get("order_id") and is_eligible_order(o)}
    line_obs = []
    for ln in lines:
        if ln.get("order_pin") != pincode or ln.get("order_id") not in eligible_ids:
            continue
        cid = ln.get("customer_id")
        if not allowed(cid):
            continue
        family = silhouette_for(ln.get("commodity_name"))
        if family == "generic":
            continue
        line_obs.append(Obs(cid=str(cid), ts=_ts(ln.get("ordered_at")), family=family,
                            commodity=str(ln.get("commodity_name") or "unknown")))
    lines_window, lines_as_of = _in_lookback(line_obs, lookback_days)

    pin_pool = Pool("pin_garments", pin_window, pin_as_of, lookback_days)
    line_pool = Pool("pin_service_lines", lines_window, lines_as_of, lookback_days)
    return RankContext(
        pincode=pincode,
        lookback_days=lookback_days,
        k=k,
        pin_garments=pin_pool,
        market_garments=Pool("market_garments", market_window, market_as_of, lookback_days),
        pin_lines=line_pool,
        customers_by_id=customers_by_id,
        rfm_by_customer={
            str(r["customer_id"]): str(r.get("rfm_segment"))
            for r in rfm_snapshots or []
            if r.get("customer_id") and r.get("rfm_segment") and not r.get("insufficient_history")
        },
        area_profile=_area_profile(pincode, pin_garment_rows, line_pool, k),
    )


def _pct_change(pool: Pool, k: int) -> Optional[float]:
    recent, prior = _window_customers(pool, lambda _o: True)
    if len(recent) < k or len(prior) < k:
        return None
    return round(len(recent) / len(prior) - 1, 4)


def _area_profile(pincode: str, garments: list[dict[str, Any]], lines: Pool, k: int) -> dict[str, Any]:
    profile = garment_profile(garments, my_brand="", k=k)
    profile.pop("brand_mix", None)
    profile.pop("competitor_share", None)
    commodity_customers: dict[str, set[str]] = defaultdict(set)
    for o in lines.obs:
        commodity_customers[o.commodity].add(o.cid)
    service_mix = [
        {"value": name, "customers": len(cids), "share": round(len(cids) / lines.customers, 4)}
        for name, cids in sorted(commodity_customers.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        if len(cids) >= k
    ][:5] if lines.customers >= k else []
    return {
        "pincode": pincode,
        "area": PIN_AREA_LABELS.get(pincode, pincode),
        "garments": profile,
        "service_commodities": service_mix,
        "apparel_customers": lines.customers if lines.customers >= k else None,
        "activity_change": _pct_change(lines, k),
        "activity_window": {
            "recent": [d.isoformat() if d else None for d in lines.window(True)],
            "prior": [d.isoformat() if d else None for d in lines.window(False)],
        },
    }


@dataclass
class SkuAttrs:
    commodity: str
    family: str
    color: str
    color_family: Optional[str]
    pattern: str
    gender: str
    styles: frozenset
    material: str
    confidence: dict[str, float]


def sku_attrs(raw: dict[str, Any]) -> SkuAttrs:
    evidence = raw.get("attribute_evidence") or {}

    def conf(name: str) -> float:
        item = evidence.get(name) if isinstance(evidence, dict) else None
        try:
            return float((item or {}).get("confidence") or 0.0)
        except (TypeError, ValueError, AttributeError):
            return 0.0

    commodity = norm_commodity(raw.get("commodity_type"))
    color = str(raw.get("primary_color") or "unknown").strip().lower()
    return SkuAttrs(
        commodity=commodity if _known(commodity) else "unknown",
        family=silhouette_for(commodity) if _known(commodity) else "generic",
        color=color if _known(color) else "unknown",
        color_family=color_family(color),
        pattern=str(raw.get("pattern") or "unknown").strip().lower(),
        gender=str(raw.get("gender") or "unknown").strip().lower(),
        styles=frozenset(str(t).strip().lower() for t in raw.get("style_tags") or [] if t),
        material=str(raw.get("material_type") or "unknown").strip().lower(),
        confidence={n: conf(n) for n in ("commodity_type", "primary_color", "material_type", "pattern", "gender")},
    )


def _credit(attr: str, sku: SkuAttrs, o: Obs) -> Optional[float]:
    """Match credit 0..1 of one observed garment for one SKU attribute; None if not comparable."""
    if attr == "commodity":
        if not _known(o.commodity) or not _known(sku.commodity):
            return None
        if o.commodity == sku.commodity:
            return 1.0
        return 0.7 if sku.family != "generic" and o.family == sku.family else 0.0
    if attr == "color":
        if not _known(o.color) or not _known(sku.color):
            return None
        if o.color == sku.color:
            return 1.0
        return 0.6 if sku.color_family and o.color_family == sku.color_family else 0.0
    if attr == "pattern":
        if not _known(o.pattern) or not _known(sku.pattern):
            return None
        return 1.0 if o.pattern == sku.pattern else 0.0
    if attr == "gender":
        if not _known(o.gender) or not _known(sku.gender):
            return None
        if o.gender == sku.gender:
            return 1.0
        return 0.5 if "unisex" in (o.gender, sku.gender) else 0.0
    if not o.styles or not sku.styles:
        return None
    return len(o.styles & sku.styles) / len(o.styles | sku.styles)


def _comparable(sku: SkuAttrs, o: Obs) -> bool:
    if sku.family == "generic" or o.family != sku.family:
        return False
    if _known(sku.color) and _known(o.color):
        return (_credit("color", sku, o) or 0.0) >= 0.6
    return True


def visual_relevance(sku: SkuAttrs, pool: Pool, k: int) -> tuple[Optional[float], dict[str, Any]]:
    detail: dict[str, Any] = {}
    num = den = 0.0
    for attr, (weight, saturation) in VISUAL_ATTRS.items():
        known_customers = 0
        matched_customers = 0
        credit_sum = 0.0
        for obs in pool.by_customer.values():
            credits = [c for c in (_credit(attr, sku, o) for o in obs) if c is not None]
            if not credits:
                continue
            known_customers += 1
            best = max(credits)
            if best > 0:
                matched_customers += 1
                credit_sum += best
        if known_customers < k:
            continue
        suppressed = matched_customers < k
        share = 0.0 if suppressed else credit_sum / known_customers
        value = min(1.0, share / saturation)
        num += weight * value
        den += weight
        detail[attr] = {
            "value": _attr_value(sku, attr),
            "share": None if suppressed else round(share, 4),
            "customers_compared": known_customers,
            "suppressed": suppressed,
            "score": round(100 * value, 1),
        }
    if not any(a in detail for a in ("commodity", "color")):
        return None, detail
    return round(100 * num / den, 1), detail


def _attr_value(sku: SkuAttrs, attr: str) -> Any:
    return {
        "commodity": sku.commodity, "color": sku.color, "pattern": sku.pattern,
        "gender": sku.gender, "style": sorted(sku.styles),
    }[attr]


def _window_customers(pool: Pool, pred: Callable[[Obs], bool]) -> tuple[set[str], set[str]]:
    r_from, r_to = pool.window(True)
    p_from, _ = pool.window(False)
    recent: set[str] = set()
    prior: set[str] = set()
    if r_from is None:
        return recent, prior
    for o in pool.obs:
        if not o.ts or not pred(o):
            continue
        if o.ts > r_from:
            recent.add(o.cid)
        elif p_from is not None and o.ts > p_from:
            prior.add(o.cid)
    return recent, prior


def _adoption_pool(sku: SkuAttrs, ctx: RankContext) -> Optional[Pool]:
    if sku.family == "generic":
        return None
    for pool in (ctx.pin_garments, ctx.pin_lines, ctx.market_garments):
        if pool.customers >= ctx.k:
            return pool
    return None


def _adopter_pred(sku: SkuAttrs, pool: Pool) -> Callable[[Obs], bool]:
    if pool.scope == "pin_service_lines":
        return lambda o: o.family == sku.family
    return lambda o: _comparable(sku, o)


def local_adoption(sku: SkuAttrs, pool: Pool, k: int) -> tuple[float, set[str], dict[str, Any]]:
    pred = _adopter_pred(sku, pool)
    adopters = {cid for cid, obs in pool.by_customer.items() if any(pred(o) for o in obs)}
    suppressed = len(adopters) < k
    n = 0 if suppressed else len(adopters)
    penetration = n / pool.customers if pool.customers else 0.0
    sat = ADOPTION_PENETRATION_SATURATION[pool.scope]
    score = 100 * (0.7 * min(1.0, penetration / sat) + 0.3 * min(1.0, math.log1p(n) / math.log1p(ADOPTION_VOLUME_SATURATION)))
    return round(score, 1), (set() if suppressed else adopters), {
        "eligible_unique_customers": n if not suppressed else None,
        "base_customers": pool.customers,
        "penetration": round(penetration, 4) if not suppressed else None,
        "observation_count": (
            sum(1 for obs in pool.by_customer.values() for o in obs if pred(o)) if not suppressed else None
        ),
        "suppressed": suppressed,
        "match_rule": "commodity family" if pool.scope == "pin_service_lines" else "commodity family + colour family",
    }


def trend_momentum(sku: SkuAttrs, pool: Pool, k: int) -> tuple[Optional[float], dict[str, Any]]:
    pred = _adopter_pred(sku, pool)
    base_r, base_p = _window_customers(pool, lambda _o: True)
    hit_r, hit_p = _window_customers(pool, pred)
    r_from, r_to = pool.window(True)
    p_from, p_to = pool.window(False)
    detail: dict[str, Any] = {
        "recent_window": [r_from.isoformat() if r_from else None, r_to.isoformat() if r_to else None],
        "prior_window": [p_from.isoformat() if p_from else None, p_to.isoformat() if p_to else None],
        "recent_customers": len(hit_r) if len(hit_r) >= k else None,
        "prior_customers": len(hit_p) if len(hit_p) >= k else None,
    }
    if len(base_r) < k or len(base_p) < k or len(hit_r) + len(hit_p) < k:
        detail["reason"] = f"fewer than k={k} eligible customers in a trend window"
        return None, detail
    share_r = len(hit_r) / len(base_r)
    share_p = len(hit_p) / len(base_p)
    momentum = (share_r - share_p) / (share_r + share_p) if share_r + share_p else 0.0
    detail.update(recent_share=round(share_r, 4), prior_share=round(share_p, 4), momentum=round(momentum, 4))
    return round(max(0.0, min(100.0, 50 + 50 * momentum)), 1), detail


def seasonal_relevance(sku: SkuAttrs, as_of: Optional[str]) -> tuple[Optional[float], dict[str, Any]]:
    reliable_material = sku.material in SEASON_MATERIALS and sku.confidence.get("material_type", 0) >= RELIABLE_ATTRIBUTE_CONFIDENCE
    reliable_commodity = _known(sku.commodity) and sku.confidence.get("commodity_type", 0) >= RELIABLE_ATTRIBUTE_CONFIDENCE
    if not as_of or not (reliable_material or reliable_commodity):
        return None, {"reason": "no reliable commodity/material attribute for seasonal rules"}
    season = SEASON_BY_MONTH[_ts(as_of).month]  # type: ignore[union-attr]
    rule = SEASON_MATERIALS[sku.material] if reliable_material else next(
        (r for r, words in SEASON_COMMODITIES.items() if sku.commodity in words), "all_season"
    )
    score = float(ALL_SEASON_SCORE if rule == "all_season" else SEASON_PROFILES[rule][season])
    return score, {"season": season, "rule": rule, "reference_date": as_of[:10],
                   "basis": "material" if reliable_material else "commodity"}


def _top_segments(adopters: set[str], ctx: RankContext) -> dict[str, Any]:
    k = ctx.k
    if len(adopters) < k:
        return {"segments": [], "rfm_segment": None, "suppressed": True}
    cells: dict[tuple[str, str], set[str]] = defaultdict(set)
    profiled = 0
    for cid in adopters:
        prof = ctx.customers_by_id.get(cid)
        if not prof:
            continue
        profiled += 1
        cells[(age_band(prof.get("age")), str(prof.get("gender") or "unknown").lower())].add(cid)
    segments = [
        {"age_band": band, "gender": gender, "customers": len(ids), "share": round(len(ids) / profiled, 4)}
        for (band, gender), ids in sorted(cells.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        if len(ids) >= k and band != "unknown"
    ][:3]
    rfm = Counter(ctx.rfm_by_customer[c] for c in adopters if c in ctx.rfm_by_customer)
    top_rfm = next(((seg, n) for seg, n in sorted(rfm.items(), key=lambda kv: (-kv[1], kv[0])) if n >= k), None)
    return {
        "segments": segments,
        "rfm_segment": {"segment": top_rfm[0], "customers": top_rfm[1],
                        "share": round(top_rfm[1] / sum(rfm.values()), 4)} if top_rfm else None,
        "suppressed": False,
    }


def _confidence(scores: dict[str, Optional[float]], pool: Optional[Pool], sku: SkuAttrs, k: int) -> str:
    if scores["visual_relevance"] is None and scores["local_adoption"] is None:
        return "insufficient_evidence"
    extraction_ok = min(sku.confidence.get("commodity_type", 0), sku.confidence.get("primary_color", 0)) >= 0.6
    present = sum(1 for v in scores.values() if v is not None)
    if (pool is not None and pool.scope == "pin_garments" and pool.customers >= 3 * k
            and present >= 3 and extraction_ok):
        return "high"
    if pool is not None and pool.scope != "market_garments" and scores["visual_relevance"] is not None:
        return "medium"
    return "low"


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _driver_text(name: str, score: float, ev: dict[str, Any], ctx: RankContext, sku: SkuAttrs) -> str:
    k = ctx.k
    if name == "visual_relevance":
        parts = [
            f"{d['value'] if not isinstance(d['value'], list) else '/'.join(d['value'])} matches "
            f"{_pct(d['share'])} of {d['customers_compared']} customers' garments"
            for a, d in ev["visual"]["attributes"].items()
            if a in ("commodity", "color") and not d["suppressed"]
        ]
        return f"visual relevance {score:.0f}/100 ({'; '.join(parts) or f'matches below k={k}'})"
    if name == "local_adoption":
        a = ev["adoption"]
        if a["suppressed"]:
            return f"local adoption {score:.0f}/100 (fewer than {k} eligible customers with comparable garments)"
        return (
            f"local adoption {score:.0f}/100 ({a['eligible_unique_customers']} of {a['base_customers']} eligible "
            f"customers own comparable {sku.family} garments, {ctx.lookback_days}-day window)"
        )
    if name == "trend_momentum":
        t = ev["trend"]
        return (
            f"trend momentum {score:.0f}/100 (comparable share {_pct(t.get('prior_share'))} in "
            f"{(t['prior_window'][0] or '')[:10]}→{(t['prior_window'][1] or '')[:10]} vs {_pct(t.get('recent_share'))} "
            f"in {(t['recent_window'][0] or '')[:10]}→{(t['recent_window'][1] or '')[:10]})"
        )
    s = ev["season"]
    return f"seasonal relevance {score:.0f}/100 ({s.get('rule', '').replace('_', ' ')} vs {s.get('season')} at data date)"


def combine(
    scores: dict[str, Optional[float]],
) -> tuple[Optional[float], dict[str, float], dict[str, Optional[float]]]:
    """Weighted opportunity over present factors, weights renormalised to sum to 1."""
    present = {n: s for n, s in scores.items() if s is not None}
    weight_sum = sum(WEIGHTS[n] for n in present)
    if not present or weight_sum <= 0:
        return None, {n: 0.0 for n in WEIGHTS}, {n: None for n in WEIGHTS}
    effective = {n: (WEIGHTS[n] / weight_sum if n in present else 0.0) for n in WEIGHTS}
    contributions = {n: (round(effective[n] * s, 2) if s is not None else None) for n, s in scores.items()}
    return round(sum(effective[n] * s for n, s in present.items()), 1), effective, contributions


def score_sku(raw_attrs: dict[str, Any], ctx: RankContext) -> dict[str, Any]:
    sku = sku_attrs(raw_attrs)
    k = ctx.k
    scores: dict[str, Optional[float]] = {name: None for name in WEIGHTS}
    missing: list[dict[str, str]] = []
    evidence: dict[str, Any] = {}

    visual_pool = ctx.pin_garments if ctx.pin_garments.customers >= k else ctx.market_garments
    if visual_pool.customers >= k:
        scores["visual_relevance"], attrs = visual_relevance(sku, visual_pool, k)
        evidence["visual"] = {"scope": visual_pool.scope, "base_customers": visual_pool.customers, "attributes": attrs}
    else:
        evidence["visual"] = {"scope": None, "base_customers": None, "attributes": {}}
    if scores["visual_relevance"] is None:
        missing.append({"factor": "visual_relevance", "reason": (
            "garment history below k" if visual_pool.customers < k
            else "commodity/colour not extracted or matches below k"
        )})

    pool = _adoption_pool(sku, ctx)
    adopters: set[str] = set()
    if pool is not None:
        scores["local_adoption"], adopters, evidence["adoption"] = local_adoption(sku, pool, k)
        scores["trend_momentum"], evidence["trend"] = trend_momentum(sku, pool, k)
        if scores["trend_momentum"] is None:
            missing.append({"factor": "trend_momentum", "reason": evidence["trend"]["reason"]})
    else:
        reason = "garment commodity not recognised" if sku.family == "generic" else "PIN history below k"
        missing.append({"factor": "local_adoption", "reason": reason})
        missing.append({"factor": "trend_momentum", "reason": reason})
        evidence["adoption"] = None
        evidence["trend"] = None

    scores["seasonal_relevance"], evidence["season"] = seasonal_relevance(sku, ctx.data_as_of)
    if scores["seasonal_relevance"] is None:
        missing.append({"factor": "seasonal_relevance", "reason": evidence["season"]["reason"]})

    confidence = _confidence(scores, pool, sku, k)
    present = {n: s for n, s in scores.items() if s is not None}
    if confidence == "insufficient_evidence":
        opportunity, effective, contributions = None, {n: 0.0 for n in WEIGHTS}, {n: None for n in WEIGHTS}
    else:
        opportunity, effective, contributions = combine(scores)

    scope = pool.scope if pool is not None else (evidence["visual"]["scope"])
    evidence.update(
        eligible_unique_customers=(evidence["adoption"] or {}).get("eligible_unique_customers"),
        observation_count=(evidence["adoption"] or {}).get("observation_count"),
        sample_size=pool.customers if pool is not None else evidence["visual"]["base_customers"],
        evidence_scope=scope,
        evidence_scope_label=SCOPE_LABELS.get(scope or "", "no eligible history"),
        trend_windows={
            "recent": (evidence["trend"] or {}).get("recent_window"),
            "prior": (evidence["trend"] or {}).get("prior_window"),
        },
        target=_top_segments(adopters, ctx),
        data_as_of=ctx.data_as_of,
    )

    limitations = ["Historical garment-demand proxy for this PIN — not a sales forecast, causal uplift or verified unmet demand."]
    if scope == "pin_service_lines":
        limitations.append("PIN garment extractions are below k; adoption and trend use service order lines, which carry commodity family but not colour or pattern.")
    if scope == "market_garments":
        limitations.append("PIN history is below k; evidence comes from all PINs and is not local.")
    if raw_attrs.get("source") == "local_heuristic":
        limitations.append("Vertex AI disabled: attributes come from a local colour/file-name heuristic with low confidence.")

    if opportunity is None:
        justification = (
            f"Insufficient evidence: no eligible history with at least k={k} customers is comparable to this "
            f"{sku.commodity if _known(sku.commodity) else 'image'} for PIN {ctx.pincode}."
        )
    else:
        ranked = sorted(present, key=lambda n: -(contributions[n] or 0))
        drivers = [_driver_text(n, present[n], evidence, ctx, sku) for n in ranked[:2]]
        rest = [_driver_text(n, present[n], evidence, ctx, sku) for n in ranked[2:]]
        justification = (
            f"Opportunity {opportunity:.1f}/100. Strongest drivers: {' and '.join(drivers)}."
            + (f" Also: {'; '.join(rest)}." if rest else "")
            + f" Evidence: {evidence['evidence_scope_label']}, data as of {(ctx.data_as_of or '')[:10]}."
            + (f" Excluded: {', '.join(m['factor'] for m in missing)}." if missing else "")
        )

    return {
        "opportunity_score": opportunity,
        "factor_scores": scores,
        "factor_weights": {n: round(w, 4) for n, w in effective.items()},
        "factor_contributions": contributions,
        "confidence": confidence,
        "evidence": evidence,
        "justification": justification,
        "missing_signals": missing,
        "limitations": limitations,
        "attributes": {
            "commodity_type": sku.commodity, "family": sku.family, "primary_color": sku.color,
            "pattern": sku.pattern, "gender": sku.gender, "material_type": sku.material,
            "style_tags": sorted(sku.styles), "source": raw_attrs.get("source"),
            "model_version": raw_attrs.get("model_version"), "confidence": sku.confidence,
        },
    }


def assign_ranks(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unique ranks 1..N by descending score; ties and unscored items by input_index."""
    ordered = sorted(
        results,
        key=lambda r: (r["opportunity_score"] is None, -(r["opportunity_score"] or 0.0), r["input_index"]),
    )
    for i, r in enumerate(ordered, start=1):
        r["rank"] = i
    return ordered


def rank_skus(items: list[dict[str, Any]], ctx: RankContext) -> list[dict[str, Any]]:
    """items: [{input_index, image_id, ..., attributes}] → exactly len(items) ranked results."""
    results = []
    for item in items:
        scored = score_sku(item.get("attributes") or {}, ctx)
        results.append({
            "input_index": item["input_index"],
            "image_id": item["image_id"],
            **{k: v for k, v in item.items() if k not in {"input_index", "image_id", "attributes"}},
            "rank": 0,
            **scored,
        })
    return assign_ranks(results)
