"""Brand campaign persistence + segment preference + ranking helpers."""

from __future__ import annotations

import json
import logging
import math
import os
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
    return campaign_id


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
            cur.execute(
                """
                UPDATE brand_campaigns
                SET status = %s, error_message = %s, updated_at = now()
                WHERE campaign_id = %s
                """,
                (status, error_message, campaign_id),
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
