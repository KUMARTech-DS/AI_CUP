"""Brand campaign persistence + segment preference + ranking helpers."""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Sequence

log = logging.getLogger("command_center.brand_campaign")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def brand_slug(brand_name: str) -> str:
    s = _SLUG_RE.sub("-", (brand_name or "").strip().lower()).strip("-")
    return s or "brand"


@contextmanager
def connect() -> Iterator[Any]:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_campaign(
    *,
    brand_name: str,
    pin_codes: Sequence[str],
    age_group: Optional[str],
    gender: Optional[str],
    rfm_segment: Optional[str],
) -> str:
    campaign_id = str(uuid.uuid4())
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO brand_campaigns (
                  campaign_id, brand_name, pin_codes, age_group, gender, rfm_segment, status
                ) VALUES (%s,%s,%s,%s,%s,%s,'draft')
                """,
                (
                    campaign_id,
                    brand_name,
                    list(pin_codes),
                    age_group or None,
                    gender or None,
                    rfm_segment or None,
                ),
            )
    seed_synthetic_impact(campaign_id)
    return campaign_id


def seed_synthetic_impact(campaign_id: str) -> None:
    """Persist demo marketing KPIs once at campaign create (no-op if already present)."""
    reach = random.randint(5_000, 80_000)
    impressions = int(reach * random.uniform(1.2, 3.5))
    ctr = round(random.uniform(0.8, 4.5), 2)
    conversions = max(1, int(impressions * (ctr / 100.0) * random.uniform(0.05, 0.25)))
    revenue = round(conversions * random.uniform(400.0, 2500.0), 2)
    lift = round(random.uniform(1.0, 12.0), 2)
    roas = round(random.uniform(1.2, 6.5), 2)
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO campaign_impact_metrics (
                      campaign_id, reach, impressions, ctr, conversions,
                      revenue, lift, roas, source
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'synthetic')
                    ON CONFLICT (campaign_id) DO NOTHING
                    """,
                    (campaign_id, reach, impressions, ctr, conversions, revenue, lift, roas),
                )
    except Exception as e:
        log.warning("seed_synthetic_impact failed for %s: %s", campaign_id, e)


def _impact_from_row(
    reach: Any,
    impressions: Any,
    ctr: Any,
    conversions: Any,
    revenue: Any,
    lift: Any,
    roas: Any,
    source: Any,
) -> Optional[dict[str, Any]]:
    if reach is None and impressions is None:
        return None
    return {
        "reach": int(reach or 0),
        "impressions": int(impressions or 0),
        "ctr": float(ctr or 0),
        "conversions": int(conversions or 0),
        "revenue": float(revenue or 0),
        "lift": float(lift or 0),
        "roas": float(roas or 0),
        "source": source or "synthetic",
    }


def insert_sku(
    *,
    sku_uuid: str,
    campaign_id: str,
    brand_name: str,
    original_filename: Optional[str],
    gcs_bucket: str,
    gcs_object_key: str,
    attributes: dict[str, Any],
    raw_result: Any,
    extract_ok: bool,
    extract_error: Optional[str] = None,
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO brand_skus (
                  sku_uuid, campaign_id, brand_name, original_filename,
                  gcs_bucket, gcs_object_key,
                  color, pattern, material, size, commodity,
                  sleeve_type, collar_type, confidence, raw_result,
                  extract_ok, extract_error
                ) VALUES (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    sku_uuid,
                    campaign_id,
                    brand_name,
                    original_filename,
                    gcs_bucket,
                    gcs_object_key,
                    attributes.get("color"),
                    attributes.get("pattern"),
                    attributes.get("material"),
                    attributes.get("size"),
                    attributes.get("name") or attributes.get("commodity"),
                    attributes.get("sleeve_type"),
                    attributes.get("collar_type"),
                    str(attributes.get("confidence")) if attributes.get("confidence") is not None else None,
                    json.dumps(raw_result) if raw_result is not None else None,
                    extract_ok,
                    extract_error,
                ),
            )


def update_sku_embedding(
    sku_uuid: str, embedding: List[float], model: str, dim: int
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE brand_skus
                SET embedding = %s::jsonb, embedding_model = %s, embedding_dim = %s
                WHERE sku_uuid = %s
                """,
                (json.dumps(embedding), model, dim, sku_uuid),
            )


def upsert_pin_scores(
    sku_uuid: str, per_pin: dict[str, Any]
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            for pin, detail in (per_pin or {}).items():
                cur.execute(
                    """
                    INSERT INTO brand_sku_pin_scores (sku_uuid, pin_code, fit_score, top_matches)
                    VALUES (%s,%s,%s,%s::jsonb)
                    ON CONFLICT (sku_uuid, pin_code) DO UPDATE SET
                      fit_score = EXCLUDED.fit_score,
                      top_matches = EXCLUDED.top_matches,
                      computed_at = now()
                    """,
                    (
                        sku_uuid,
                        str(pin),
                        float(detail.get("pin_fit") or 0),
                        json.dumps(detail.get("top_matches") or []),
                    ),
                )


def set_campaign_status(
    campaign_id: str, status: str, error_message: Optional[str] = None
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            extra = ""
            if status == "started":
                extra = ", started_at = COALESCE(started_at, now()), ignored_at = NULL"
            elif status == "ignored":
                extra = ", ignored_at = COALESCE(ignored_at, now())"
            cur.execute(
                f"""
                UPDATE brand_campaigns
                SET status = %s, error_message = %s, updated_at = now(){extra}
                WHERE campaign_id = %s
                """,
                (status, error_message, campaign_id),
            )


def get_campaign(campaign_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.campaign_id::text, c.brand_name, c.pin_codes, c.age_group, c.gender,
                       c.rfm_segment, c.status, c.error_message, c.title,
                       c.started_at, c.ignored_at, c.created_at, c.updated_at,
                       COALESCE(c.eligible_users, 0),
                       COALESCE(c.recommendations_computed, 0),
                       COALESCE(c.style_feed_serves, 0),
                       i.reach, i.impressions, i.ctr, i.conversions,
                       i.revenue, i.lift, i.roas, i.source
                FROM brand_campaigns c
                LEFT JOIN campaign_impact_metrics i ON i.campaign_id = c.campaign_id
                WHERE c.campaign_id = %s
                """,
                (campaign_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _campaign_row(row)


def _campaign_row(row: Sequence[Any]) -> dict[str, Any]:
    pins = row[2] or []
    if not isinstance(pins, list):
        pins = list(pins) if pins else []
    out: dict[str, Any] = {
        "campaign_id": row[0],
        "brand_name": row[1],
        "pin_codes": [str(p) for p in pins],
        "age_group": row[3],
        "gender": row[4],
        "rfm_segment": row[5],
        "status": row[6],
        "error_message": row[7],
        "title": row[8],
        "started_at": row[9].isoformat() if row[9] else None,
        "ignored_at": row[10].isoformat() if row[10] else None,
        "created_at": row[11].isoformat() if row[11] else None,
        "updated_at": row[12].isoformat() if row[12] else None,
        "eligible_users": int(row[13] or 0),
        "recommendations_computed": int(row[14] or 0),
        "style_feed_serves": int(row[15] or 0),
    }
    if len(row) > 16:
        impact = _impact_from_row(
            row[16], row[17], row[18], row[19], row[20], row[21], row[22], row[23]
        )
        if impact:
            out["impact"] = impact
    return out


def list_campaigns(
    *,
    brand_name: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            if brand_name:
                cur.execute(
                    """
                    SELECT c.campaign_id::text, c.brand_name, c.pin_codes, c.age_group,
                           c.gender, c.rfm_segment, c.status, c.error_message, c.title,
                           c.started_at, c.ignored_at, c.created_at, c.updated_at,
                           COALESCE(c.eligible_users, 0),
                           COALESCE(c.recommendations_computed, 0),
                           COALESCE(c.style_feed_serves, 0),
                           i.reach, i.impressions, i.ctr, i.conversions,
                           i.revenue, i.lift, i.roas, i.source,
                           (SELECT COUNT(*) FROM brand_skus s WHERE s.campaign_id = c.campaign_id)
                    FROM brand_campaigns c
                    LEFT JOIN campaign_impact_metrics i ON i.campaign_id = c.campaign_id
                    WHERE lower(c.brand_name) = lower(%s)
                    ORDER BY c.created_at DESC
                    LIMIT %s
                    """,
                    (brand_name, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT c.campaign_id::text, c.brand_name, c.pin_codes, c.age_group,
                           c.gender, c.rfm_segment, c.status, c.error_message, c.title,
                           c.started_at, c.ignored_at, c.created_at, c.updated_at,
                           COALESCE(c.eligible_users, 0),
                           COALESCE(c.recommendations_computed, 0),
                           COALESCE(c.style_feed_serves, 0),
                           i.reach, i.impressions, i.ctr, i.conversions,
                           i.revenue, i.lift, i.roas, i.source,
                           (SELECT COUNT(*) FROM brand_skus s WHERE s.campaign_id = c.campaign_id)
                    FROM brand_campaigns c
                    LEFT JOIN campaign_impact_metrics i ON i.campaign_id = c.campaign_id
                    ORDER BY c.created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            out = []
            for row in cur.fetchall():
                item = _campaign_row(row[:24])
                item["sku_count"] = int(row[24] or 0)
                out.append(item)
            return out


def list_campaign_skus(campaign_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sku_uuid::text, brand_name, original_filename, gcs_bucket,
                       gcs_object_key, color, pattern, material, size, commodity,
                       extract_ok, extract_error, created_at
                FROM brand_skus
                WHERE campaign_id = %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (campaign_id, limit),
            )
            rows = []
            for r in cur.fetchall():
                rows.append(
                    {
                        "sku_uuid": r[0],
                        "brand_name": r[1],
                        "original_filename": r[2],
                        "gcs_bucket": r[3],
                        "gcs_object_key": r[4],
                        "attributes": {
                            "color": r[5],
                            "pattern": r[6],
                            "material": r[7],
                            "size": r[8],
                            "commodity": r[9],
                            "name": r[9],
                        },
                        "extract_ok": bool(r[10]),
                        "extract_error": r[11],
                        "created_at": r[12].isoformat() if r[12] else None,
                    }
                )
            return rows


def eligible_user_uuids_for_campaign(
    campaign: dict[str, Any],
    *,
    cap: Optional[int] = None,
) -> list[str]:
    """
    Users whose wardrobe first-sight PIN is in campaign pin_codes.
    Age/gender/RFM are campaign audience metadata; PIN is the enforceable
    user-level key available on user_wardrobe today.
    """
    limit = cap if cap is not None else int(os.getenv("CAMPAIGN_ELIGIBLE_USER_CAP", "200"))
    pins = [str(p).strip() for p in (campaign.get("pin_codes") or []) if str(p).strip()]
    if not pins:
        return []
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT user_uuid
                FROM user_wardrobe
                WHERE pin_code_at_first_sight = ANY(%s)
                  AND user_uuid IS NOT NULL
                  AND trim(user_uuid) <> ''
                ORDER BY user_uuid
                LIMIT %s
                """,
                (pins, limit),
            )
            return [str(r[0]) for r in cur.fetchall()]


def upsert_user_recommendation(
    *,
    campaign_id: str,
    user_uuid: str,
    brand_name: str,
    fit_score: Optional[float],
    payload: dict[str, Any],
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO campaign_user_recommendations (
                  campaign_id, user_uuid, brand_name, fit_score, payload
                ) VALUES (%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (campaign_id, user_uuid) DO UPDATE SET
                  brand_name = EXCLUDED.brand_name,
                  fit_score = EXCLUDED.fit_score,
                  payload = EXCLUDED.payload,
                  computed_at = now()
                """,
                (
                    campaign_id,
                    user_uuid,
                    brand_name,
                    fit_score,
                    json.dumps(payload),
                ),
            )


def set_campaign_impact(
    campaign_id: str,
    *,
    eligible_users: Optional[int] = None,
    recommendations_computed: Optional[int] = None,
    bump_style_feed_serves: int = 0,
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            sets = ["updated_at = now()"]
            args: list[Any] = []
            if eligible_users is not None:
                sets.append("eligible_users = %s")
                args.append(int(eligible_users))
            if recommendations_computed is not None:
                sets.append("recommendations_computed = %s")
                args.append(int(recommendations_computed))
            if bump_style_feed_serves:
                sets.append("style_feed_serves = COALESCE(style_feed_serves, 0) + %s")
                args.append(int(bump_style_feed_serves))
            args.append(campaign_id)
            cur.execute(
                f"UPDATE brand_campaigns SET {', '.join(sets)} WHERE campaign_id = %s",
                tuple(args),
            )


def list_started_recs_for_user(user_uuid: str, *, limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.campaign_id::text, r.user_uuid, r.brand_name, r.fit_score,
                       r.payload, r.computed_at, c.status
                FROM campaign_user_recommendations r
                JOIN brand_campaigns c ON c.campaign_id = r.campaign_id
                WHERE r.user_uuid = %s AND c.status = 'started'
                ORDER BY COALESCE(r.fit_score, 0) DESC, r.computed_at DESC
                LIMIT %s
                """,
                (user_uuid, limit),
            )
            out = []
            for row in cur.fetchall():
                payload = row[4]
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                out.append(
                    {
                        "campaign_id": row[0],
                        "user_uuid": row[1],
                        "brand_name": row[2],
                        "fit_score": float(row[3]) if row[3] is not None else None,
                        "payload": payload,
                        "computed_at": row[5].isoformat() if row[5] else None,
                    }
                )
            return out


def ensure_campaign_title(campaign_id: str, brand_name: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE brand_campaigns
                SET title = COALESCE(NULLIF(trim(title), ''), %s)
                WHERE campaign_id = %s
                """,
                (f"{brand_name} · 1P", campaign_id),
            )


def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


def _cell_matches(
    cell: dict[str, Any],
    *,
    pin_codes: Sequence[str],
    age_group: Optional[str],
    gender: Optional[str],
    rfm_segment: Optional[str],
) -> bool:
    pins = {str(p).strip() for p in pin_codes if str(p).strip()}
    cell_pin = str(cell.get("pin_code") or cell.get("pin") or "").strip()
    if pins and cell_pin and cell_pin not in pins:
        return False
    if age_group:
        ca = _norm(cell.get("age_group") or cell.get("age"))
        if ca and ca != _norm(age_group):
            return False
    if gender:
        cg = _norm(cell.get("gender"))
        tg = _norm(gender)
        if cg and tg and cg[0] != tg[0]:
            return False
    if rfm_segment:
        cr = _norm(cell.get("rfm_segment") or cell.get("rfm"))
        if cr and cr != _norm(rfm_segment):
            return False
    return True


def build_segment_profile(
    metrics: dict[str, Any],
    *,
    pin_codes: Sequence[str],
    age_group: Optional[str],
    gender: Optional[str],
    rfm_segment: Optional[str],
) -> dict[str, Any]:
    """Preference counts from metrics garment cells filtered by campaign audience."""
    cells = metrics.get("garment_segment_cells") or metrics.get("garment_cells") or []
    if not isinstance(cells, list):
        cells = []

    filtered = [
        c
        for c in cells
        if isinstance(c, dict)
        and _cell_matches(
            c,
            pin_codes=pin_codes,
            age_group=age_group,
            gender=gender,
            rfm_segment=rfm_segment,
        )
    ]
    # Progressive relax if empty (keep PINs if possible)
    if not filtered and (age_group or gender or rfm_segment):
        filtered = [
            c
            for c in cells
            if isinstance(c, dict)
            and _cell_matches(
                c, pin_codes=pin_codes, age_group=None, gender=None, rfm_segment=None
            )
        ]

    dist: dict[str, dict[str, float]] = {
        "color": {},
        "pattern": {},
        "material": {},
        "size": {},
        "commodity": {},
        "brand": {},
    }
    key_map = {
        "color": ("colors", "color"),
        "pattern": ("patterns", "pattern"),
        "material": ("materials", "material"),
        "size": ("sizes", "size"),
        "commodity": ("commodities", "commodity", "name"),
        "brand": ("brands", "brand"),
    }

    for cell in filtered:
        n = float(cell.get("garments") or cell.get("count") or 1)
        for dim, keys in key_map.items():
            pairs = None
            for k in keys:
                if isinstance(cell.get(k), list):
                    pairs = cell.get(k)
                    break
            if pairs:
                for p in pairs:
                    if isinstance(p, dict):
                        val = p.get("value") or p.get("name")
                        cnt = float(p.get("count") or n)
                    else:
                        val, cnt = p, n
                    if not val:
                        continue
                    dist[dim][_norm(val)] = dist[dim].get(_norm(val), 0) + cnt
            else:
                for k in keys:
                    if cell.get(k) and not isinstance(cell.get(k), list):
                        val = cell.get(k)
                        dist[dim][_norm(val)] = dist[dim].get(_norm(val), 0) + n
                        break

    return {
        "cell_count": len(filtered),
        "distributions": dist,
        "filters": {
            "pin_codes": list(pin_codes),
            "age_group": age_group,
            "gender": gender,
            "rfm_segment": rfm_segment,
        },
    }


def attr_fit_score(attrs: dict[str, Any], profile: dict[str, Any]) -> float:
    """0–1 weighted preference overlap (mirrors Ads Lab matchScore idea)."""
    weights = {
        "color": 25,
        "pattern": 20,
        "commodity": 20,
        "material": 15,
        "size": 10,
        "brand": 10,
    }
    dist = (profile or {}).get("distributions") or {}
    mapping = {
        "color": "color",
        "pattern": "pattern",
        "commodity": "name",
        "material": "material",
        "size": "size",
        "brand": "brand",
    }
    total_w = 0.0
    scored = 0.0
    for dim, attr_key in mapping.items():
        distribution = dist.get(dim) or {}
        pairs = [(k, float(v)) for k, v in distribution.items() if float(v) > 0]
        val = _norm(attrs.get(attr_key) or attrs.get(dim))
        if not val or not pairs:
            continue
        mx = max(v for _, v in pairs) or 1.0
        found = next((v for k, v in pairs if k == val), 0.0)
        ratio = found / mx
        w = weights[dim]
        scored += ratio * w
        total_w += w
    if total_w <= 0:
        return 0.0
    return float(scored / total_w)


def blend_rank_score(
    *, sim_fit: float, freq_coverage: float, attr_fit: float
) -> float:
    return round(
        100.0 * (0.55 * sim_fit + 0.15 * freq_coverage + 0.30 * attr_fit), 2
    )


def freq_coverage_from_compare(per_pin: dict[str, Any]) -> float:
    """Share of PIN observations covered by top matches (soft 0–1)."""
    if not per_pin:
        return 0.0
    scores = []
    for detail in per_pin.values():
        total = float(detail.get("total_observations") or 0)
        top = detail.get("top_matches") or []
        covered = sum(float(m.get("observation_count") or 0) for m in top)
        if total <= 0:
            scores.append(0.0)
        else:
            scores.append(min(1.0, covered / total))
    return float(sum(scores) / len(scores)) if scores else 0.0
