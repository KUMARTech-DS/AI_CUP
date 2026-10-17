"""Feature 2 — Style Assistant: wardrobe × brand SKU outfit recommendations."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import timedelta
from typing import Any, List, Optional, Sequence

import brand_campaign as brand_camp

log = logging.getLogger("command_center.style_assistant")

COSINE_MATCH_THRESHOLD = float(os.getenv("STYLE_COSINE_THRESHOLD", "0.75"))
SIGNED_URL_MINUTES = int(os.getenv("STYLE_SIGNED_URL_MINUTES", "60"))

# Outfit completion: same bucket must never pair (no shirt↔shirt / jeans↔jeans).
CATEGORY_BUCKETS = ("top", "bottom", "outer", "dress", "footwear", "accessory", "unknown")

COMPLEMENTARY: dict[str, frozenset[str]] = {
    "top": frozenset({"bottom", "outer", "footwear", "accessory", "dress"}),
    "bottom": frozenset({"top", "outer", "footwear", "accessory"}),
    "outer": frozenset({"top", "bottom", "dress", "footwear", "accessory"}),
    "dress": frozenset({"outer", "footwear", "accessory", "bottom"}),
    "footwear": frozenset({"top", "bottom", "outer", "dress", "accessory"}),
    "accessory": frozenset({"top", "bottom", "outer", "dress", "footwear"}),
    "unknown": frozenset(),  # resolved per-item with soft rules
}

# Longer / more specific phrases first
_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "outer",
        (
            "overshirt",
            "overcoat",
            "trench",
            "parka",
            "blazer",
            "bomber",
            "windbreaker",
            "raincoat",
            "hoodie",
            "sweatshirt",
            "cardigan",
            "jacket",
            "coat",
            "vest",
            "gilet",
            "shrug",
        ),
    ),
    (
        "bottom",
        (
            "jeans",
            "denim",
            "chinos",
            "trousers",
            "pants",
            "leggings",
            "joggers",
            "shorts",
            "skirt",
            "culottes",
            "track pant",
            "trackpants",
        ),
    ),
    (
        "dress",
        ("dress", "gown", "jumpsuit", "romper", "playsuit", "saree", "kurta set"),
    ),
    (
        "footwear",
        (
            "sneaker",
            "shoes",
            "shoe",
            "boot",
            "sandal",
            "loafer",
            "heel",
            "footwear",
            "slipper",
        ),
    ),
    (
        "accessory",
        (
            "belt",
            "scarf",
            "hat",
            "cap",
            "bag",
            "handbag",
            "watch",
            "tie",
            "sock",
            "gloves",
            "jewellery",
            "jewelry",
            "necklace",
            "sunglass",
        ),
    ),
    (
        "top",
        (
            "t-shirt",
            "tshirt",
            "tee",
            "shirt",
            "blouse",
            "polo",
            "kurta",
            "top",
            "tank",
            "camisole",
            "sweater",
            "jumper",
            "tunic",
        ),
    ),
]


def infer_category_bucket(*texts: Any) -> str:
    """Map free-text garment type / commodity / filename → outfit bucket."""
    blob = " ".join(str(t or "") for t in texts).lower()
    blob = re.sub(r"[_\-.]+", " ", blob)
    if not blob.strip():
        return "unknown"
    for bucket, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in blob:
                return bucket
    return "unknown"


def categories_complementary(a: str, b: str) -> bool:
    """True if a and b can complete an outfit together (never same bucket)."""
    ca = a if a in COMPLEMENTARY else "unknown"
    cb = b if b in COMPLEMENTARY else "unknown"
    if ca == "unknown" or cb == "unknown":
        # Unknown × known: allow (cannot prove same category).
        # Unknown × unknown: allow soft; ranking still prefers complementary when known.
        return ca != cb or ca == "unknown"
    if ca == cb:
        return False
    return cb in COMPLEMENTARY[ca]


def annotate_item_category(item: dict[str, Any]) -> str:
    attrs = item.get("attributes") or {}
    bucket = infer_category_bucket(
        attrs.get("commodity"),
        attrs.get("name"),
        attrs.get("garment_type"),
        attrs.get("category"),
        item.get("label"),
        item.get("commodity_type"),
        item.get("original_filename"),
        item.get("gcs_object_key"),
    )
    item["category_bucket"] = bucket
    return bucket


def _parse_embedding(raw: Any) -> Optional[List[float]]:
    if raw is None:
        return None
    if isinstance(raw, list):
        try:
            return [float(x) for x in raw]
        except (TypeError, ValueError):
            return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [float(x) for x in parsed]
        except Exception:
            return None
    return None


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        fx, fy = float(x), float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 0 or nb <= 0:
        return 0.0
    return float(dot / (math.sqrt(na) * math.sqrt(nb)))


def sim_fit_01(cosine: float) -> float:
    """Map cosine (−1..1) to 0..1 for blend_rank_score."""
    return max(0.0, min(1.0, (float(cosine) + 1.0) / 2.0))


def list_style_users(*, limit: int = 200) -> list[str]:
    with brand_camp.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_uuid, COUNT(*) AS n
                FROM user_wardrobe
                GROUP BY user_uuid
                ORDER BY n DESC, user_uuid
                LIMIT %s
                """,
                (limit,),
            )
            return [str(r[0]) for r in cur.fetchall() if r[0]]


def list_style_brands(*, limit: int = 200) -> list[str]:
    with brand_camp.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT brand_name, COUNT(*) AS n
                FROM brand_skus
                WHERE brand_name IS NOT NULL AND trim(brand_name) <> ''
                GROUP BY brand_name
                ORDER BY n DESC, brand_name
                LIMIT %s
                """,
                (limit,),
            )
            return [str(r[0]) for r in cur.fetchall() if r[0]]


def _wardrobe_rows_from_cursor(cur_rows: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for r in cur_rows:
        emb = _parse_embedding(r[4])
        brand = r[1] or (r[12] if len(r) > 12 else None)
        commodity = r[8] if len(r) > 8 else None
        attrs: dict[str, Any] = {}
        if brand:
            attrs["brand"] = brand
        if commodity:
            attrs["commodity"] = commodity
            attrs["name"] = commodity
        if len(r) > 9 and r[9]:
            attrs["color"] = r[9]
        if len(r) > 10 and r[10]:
            attrs["material"] = r[10]
        if len(r) > 11 and r[11]:
            attrs["pattern"] = r[11]
        label = commodity or brand or "Wardrobe piece"
        first_key = r[7] if len(r) > 7 else None
        item = {
            "garment_id": r[0],
            "brand_name": brand,
            "gcs_bucket": r[2],
            "gcs_object_key": r[3] or first_key,
            "embedding": emb,
            "pin_code": r[5],
            "observation_count_total": int(r[6] or 0),
            "commodity_type": commodity,
            "label": label,
            "attributes": attrs,
        }
        annotate_item_category(item)
        rows.append(item)
    return rows


def load_user_wardrobe_rich(user_uuid: str, *, limit: int = 80) -> list[dict[str, Any]]:
    """Load wardrobe; enrich commodity_type from garment_instances when GCS key matches."""
    with brand_camp.connect() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT * FROM (
                      SELECT DISTINCT ON (ug.garment_id)
                             ug.garment_id::text,
                             ug.brand_name,
                             ug.representative_gcs_bucket,
                             ug.representative_gcs_object_key,
                             ug.embedding,
                             uw.pin_code_at_first_sight,
                             ug.observation_count_total,
                             uw.first_gcs_object_key,
                             ginst.commodity_type,
                             ginst.primary_color,
                             ginst.material_type,
                             ginst.pattern,
                             ginst.brand_name_raw,
                             ug.updated_at
                      FROM user_wardrobe uw
                      JOIN unique_garments ug ON ug.garment_id = uw.garment_id
                      LEFT JOIN garment_images gim ON (
                          gim.gcs_object_key = ug.representative_gcs_object_key
                          OR gim.gcs_object_key = uw.first_gcs_object_key
                      )
                      LEFT JOIN garment_instances ginst ON ginst.id = gim.garment_instance_id
                        AND ginst.deleted_at IS NULL
                      WHERE uw.user_uuid = %s
                      ORDER BY ug.garment_id, ginst.updated_at DESC NULLS LAST
                    ) enriched
                    ORDER BY updated_at DESC NULLS LAST
                    LIMIT %s
                    """,
                    (user_uuid, limit),
                )
                return _wardrobe_rows_from_cursor(cur.fetchall())
            except Exception as exc:
                log.warning(
                    "wardrobe enrich join failed (%s); falling back without extract attrs",
                    exc,
                )
                conn.rollback()
                cur.execute(
                    """
                    SELECT ug.garment_id::text,
                           ug.brand_name,
                           ug.representative_gcs_bucket,
                           ug.representative_gcs_object_key,
                           ug.embedding,
                           uw.pin_code_at_first_sight,
                           ug.observation_count_total,
                           uw.first_gcs_object_key
                    FROM user_wardrobe uw
                    JOIN unique_garments ug ON ug.garment_id = uw.garment_id
                    WHERE uw.user_uuid = %s
                    ORDER BY ug.updated_at DESC
                    LIMIT %s
                    """,
                    (user_uuid, limit),
                )
                return _wardrobe_rows_from_cursor(cur.fetchall())


def _sku_rows_to_items(rows: list[Any]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        attrs = {
            "color": r[4],
            "pattern": r[5],
            "material": r[6],
            "size": r[7],
            "name": r[8],
            "commodity": r[8],
            "sleeve_type": r[9],
            "collar_type": r[10],
            "brand": r[1],
        }
        item = {
            "sku_uuid": r[0],
            "brand_name": r[1],
            "gcs_bucket": r[2],
            "gcs_object_key": r[3],
            "attributes": attrs,
            "embedding": _parse_embedding(r[11]),
            "original_filename": r[12],
            "extract_ok": bool(r[13]),
            "label": r[8] or r[12] or r[1] or "SKU",
        }
        annotate_item_category(item)
        out.append(item)
    return out


def list_brand_skus(brand_name: str, *, limit: int = 80) -> list[dict[str, Any]]:
    with brand_camp.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sku_uuid::text, brand_name, gcs_bucket, gcs_object_key,
                       color, pattern, material, size, commodity,
                       sleeve_type, collar_type, embedding, original_filename,
                       extract_ok
                FROM brand_skus
                WHERE lower(brand_name) = lower(%s)
                ORDER BY
                  CASE WHEN embedding IS NOT NULL THEN 0 ELSE 1 END,
                  CASE WHEN extract_ok THEN 0 ELSE 1 END,
                  created_at DESC
                LIMIT %s
                """,
                (brand_name, limit),
            )
            return _sku_rows_to_items(cur.fetchall())


def list_campaign_skus_rich(campaign_id: str, *, limit: int = 80) -> list[dict[str, Any]]:
    with brand_camp.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sku_uuid::text, brand_name, gcs_bucket, gcs_object_key,
                       color, pattern, material, size, commodity,
                       sleeve_type, collar_type, embedding, original_filename,
                       extract_ok
                FROM brand_skus
                WHERE campaign_id = %s
                ORDER BY
                  CASE WHEN embedding IS NOT NULL THEN 0 ELSE 1 END,
                  CASE WHEN extract_ok THEN 0 ELSE 1 END,
                  created_at ASC
                LIMIT %s
                """,
                (campaign_id, limit),
            )
            return _sku_rows_to_items(cur.fetchall())


def build_wardrobe_taste_profile(wardrobe: list[dict[str, Any]]) -> dict[str, Any]:
    dist: dict[str, dict[str, float]] = {
        "color": {},
        "pattern": {},
        "material": {},
        "size": {},
        "commodity": {},
        "brand": {},
    }
    for item in wardrobe:
        attrs = item.get("attributes") or {}
        pairs = {
            "color": attrs.get("color"),
            "pattern": attrs.get("pattern"),
            "material": attrs.get("material"),
            "size": attrs.get("size"),
            "commodity": attrs.get("name") or attrs.get("commodity"),
            "brand": item.get("brand_name") or attrs.get("brand"),
        }
        for dim, raw in pairs.items():
            val = brand_camp._norm(raw)
            if not val:
                continue
            dist[dim][val] = dist[dim].get(val, 0.0) + 1.0
    return {"cell_count": len(wardrobe), "distributions": dist}


def _complementary_wardrobe(
    sku: dict[str, Any], wardrobe: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Wardrobe pieces that can complete an outfit with this SKU (not same category)."""
    sku_cat = sku.get("category_bucket") or annotate_item_category(sku)
    allowed = []
    for w in wardrobe:
        w_cat = w.get("category_bucket") or annotate_item_category(w)
        if categories_complementary(sku_cat, w_cat):
            # Extra hard block: identical non-unknown buckets
            if sku_cat != "unknown" and w_cat != "unknown" and sku_cat == w_cat:
                continue
            allowed.append(w)
    return allowed


def _same_category_wardrobe(
    sku: dict[str, Any], wardrobe: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Wardrobe pieces in the same category — for 'you may like similar' suggestions."""
    sku_cat = sku.get("category_bucket") or annotate_item_category(sku)
    if sku_cat == "unknown":
        return []
    return [
        w
        for w in wardrobe
        if (w.get("category_bucket") or annotate_item_category(w)) == sku_cat
    ]


def _score_sku_against_partners(
    sku: dict[str, Any],
    partners: list[dict[str, Any]],
    profile: dict[str, Any],
) -> Optional[dict[str, Any]]:
    if not partners:
        return None
    partners_emb = [w for w in partners if w.get("embedding")]
    sku_emb = sku.get("embedding")
    best_cos = -1.0
    best_matches: list[tuple[str, float]] = []

    if sku_emb and partners_emb:
        for w in partners_emb:
            cos = cosine_similarity(sku_emb, w["embedding"] or [])
            best_matches.append((w["garment_id"], cos))
            if cos > best_cos:
                best_cos = cos
        best_matches.sort(key=lambda t: t[1], reverse=True)
        top_own = [gid for gid, _ in best_matches[:2]]
        sim = sim_fit_01(best_cos)
        above = sum(1 for _, c in best_matches if c >= COSINE_MATCH_THRESHOLD)
        freq = above / max(1, len(best_matches))
    else:
        sim = 0.0
        freq = 0.0
        top_own = [partners[0]["garment_id"]]
        if len(partners) > 1:
            top_own.append(partners[1]["garment_id"])
        best_cos = -1.0

    a_fit = brand_camp.attr_fit_score(sku.get("attributes") or {}, profile)
    score = brand_camp.blend_rank_score(
        sim_fit=sim, freq_coverage=freq, attr_fit=a_fit
    )
    return {
        "sku": sku,
        "score": score,
        "sim_fit": round(sim, 4),
        "freq_coverage": round(freq, 4),
        "attr_fit": round(a_fit, 4),
        "best_cosine": round(best_cos, 4) if best_cos > -1 else None,
        "own_garment_ids": top_own,
        "sku_category": sku.get("category_bucket"),
        "own_categories": [
            next(
                (
                    w.get("category_bucket")
                    for w in partners
                    if w["garment_id"] == gid
                ),
                "unknown",
            )
            for gid in top_own
        ],
    }


def sku_buy_fields(sku: dict[str, Any]) -> dict[str, Any]:
    """Fields for the Buy this item CTA (optional URL template)."""
    tmpl = (os.getenv("STYLE_BUY_URL_TEMPLATE") or "").strip()
    sku_uuid = str(sku.get("sku_uuid") or "")
    brand = str(sku.get("brand_name") or "")
    buy_url = None
    if tmpl:
        try:
            buy_url = tmpl.format(
                sku_uuid=sku_uuid,
                brand_name=brand,
                brand=brand,
            )
        except Exception:
            buy_url = tmpl
    return {
        "sku_uuid": sku_uuid,
        "brand_name": brand,
        "label": sku.get("label"),
        "gcs_bucket": sku.get("gcs_bucket"),
        "gcs_object_key": sku.get("gcs_object_key"),
        "image_url": sku.get("image_url"),
        "attributes": sku.get("attributes") or {},
        "category_bucket": sku.get("category_bucket"),
        "buy_url": buy_url,
    }


def rank_skus_for_user(
    wardrobe: list[dict[str, Any]],
    skus: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Rank brand SKUs to *complete* a wardrobe outfit.
    Same category pairs (shirt↔shirt, jeans↔jeans) are excluded.
    Similarity is computed only against complementary wardrobe pieces.
    """
    for w in wardrobe:
        annotate_item_category(w)
    for s in skus:
        annotate_item_category(s)

    profile = build_wardrobe_taste_profile(wardrobe)
    ranked: list[dict[str, Any]] = []

    for sku in skus:
        partners = _complementary_wardrobe(sku, wardrobe)
        if not partners:
            log.debug(
                "skip sku=%s cat=%s — no complementary wardrobe pieces",
                sku.get("sku_uuid"),
                sku.get("category_bucket"),
            )
            continue
        row = _score_sku_against_partners(sku, partners, profile)
        if row:
            ranked.append(row)

    ranked.sort(key=lambda r: (-(r["score"] or 0), r["sku"]["sku_uuid"]))
    return ranked[:limit]


def rank_similar_skus_for_user(
    wardrobe: list[dict[str, Any]],
    skus: list[dict[str, Any]],
    *,
    limit: int = 12,
    exclude_sku_uuids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """
    Rank brand SKUs similar to what the user already owns (same category).
    'Since you like this, you may like these.'
    """
    for w in wardrobe:
        annotate_item_category(w)
    for s in skus:
        annotate_item_category(s)

    exclude = exclude_sku_uuids or set()
    profile = build_wardrobe_taste_profile(wardrobe)
    ranked: list[dict[str, Any]] = []

    for sku in skus:
        if sku.get("sku_uuid") in exclude:
            continue
        twins = _same_category_wardrobe(sku, wardrobe)
        if not twins:
            continue
        row = _score_sku_against_partners(sku, twins, profile)
        if row:
            ranked.append(row)

    ranked.sort(key=lambda r: (-(r["score"] or 0), r["sku"]["sku_uuid"]))
    return ranked[:limit]


def build_similar_likes(
    *,
    wardrobe: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    brand_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Template cards for same-category 'you may like' suggestions."""
    wardrobe_by_id = {w["garment_id"]: w for w in wardrobe}
    likes: list[dict[str, Any]] = []
    for r in ranked[:limit]:
        sku = r["sku"]
        own_ids = [g for g in (r.get("own_garment_ids") or []) if g in wardrobe_by_id][:1]
        if not own_ids:
            continue
        liked = wardrobe_by_id[own_ids[0]]
        color = (sku.get("attributes") or {}).get("color")
        color_bit = f" in {color}" if color else ""
        sku_label = sku.get("label") or "this piece"
        liked_label = liked.get("label") or "this piece"
        cat = sku.get("category_bucket") or "piece"
        likes.append(
            {
                "rank": len(likes) + 1,
                "score": r["score"],
                "sim_fit": r["sim_fit"],
                "attr_fit": r["attr_fit"],
                "liked": {
                    "garment_id": liked["garment_id"],
                    "label": liked.get("label"),
                    "brand_name": liked.get("brand_name"),
                    "category_bucket": liked.get("category_bucket"),
                    "gcs_object_key": liked.get("gcs_object_key"),
                    "image_url": liked.get("image_url"),
                },
                "buy": sku_buy_fields(sku),
                "title": f"Since you like your {liked_label}",
                "description": (
                    f"You already own {liked_label} — you may also like "
                    f"{sku_label}{color_bit} from {brand_name}."
                ),
                "why_it_works": (
                    f"Same category ({cat}) with visual/style fit score {r['score']}."
                ),
            }
        )
    return likes


def signed_image_url(
    bucket: Optional[str], object_key: Optional[str], *, minutes: int = SIGNED_URL_MINUTES
) -> Optional[str]:
    if not bucket or not object_key:
        return None
    try:
        from google.cloud import storage  # type: ignore
        import google.auth  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore

        credentials, _ = google.auth.default()
        credentials.refresh(Request())
        client = storage.Client(credentials=credentials)
        blob = client.bucket(bucket).blob(object_key)
        kwargs: dict[str, Any] = {
            "version": "v4",
            "expiration": timedelta(minutes=minutes),
            "method": "GET",
        }
        email = getattr(credentials, "service_account_email", None)
        token = getattr(credentials, "token", None)
        if email and token:
            kwargs["service_account_email"] = email
            kwargs["access_token"] = token
        return blob.generate_signed_url(**kwargs)
    except Exception as exc:
        log.warning("signed URL failed gs://%s/%s: %s", bucket, object_key, exc)
        return None


def attach_image_urls(items: list[dict[str, Any]]) -> None:
    for item in items:
        item["image_url"] = signed_image_url(
            item.get("gcs_bucket"), item.get("gcs_object_key")
        )


_JSON_BLOCK = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK.search(text)
    if not m:
        raise ValueError("no JSON in model response")
    return json.loads(m.group(0))


def build_llm_outfits(
    *,
    wardrobe: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    brand_name: str,
    user_uuid: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Ask Vertex for outfit copy; fall back to template looks on failure."""
    wardrobe_by_id = {w["garment_id"]: w for w in wardrobe}
    sku_by_id = {r["sku"]["sku_uuid"]: r for r in ranked}

    context = {
        "user_uuid": user_uuid,
        "brand_name": brand_name,
        "wardrobe": [
            {
                "garment_id": w["garment_id"],
                "label": w.get("label"),
                "brand_name": w.get("brand_name"),
                "category_bucket": w.get("category_bucket"),
                "attributes": w.get("attributes") or {},
            }
            for w in wardrobe[:40]
        ],
        "ranked_skus": [
            {
                "sku_uuid": r["sku"]["sku_uuid"],
                "label": r["sku"].get("label"),
                "brand_name": r["sku"].get("brand_name"),
                "category_bucket": r["sku"].get("category_bucket"),
                "attributes": r["sku"].get("attributes") or {},
                "score": r["score"],
                "suggested_own_garment_ids": r["own_garment_ids"],
            }
            for r in ranked[: max(limit, 8)]
        ],
        "rules": {
            "max_looks": limit,
            "own_count": "1 or 2 garment_ids from wardrobe only",
            "buy_count": "exactly 1 sku_uuid from ranked_skus only",
            "pairing": (
                "COMPLETE the outfit — never pair the same category_bucket "
                "(no shirt+shirt, no jeans+jeans). Prefer top+bottom, top+outer, "
                "bottom+outer, etc. Use suggested_own_garment_ids when complementary."
            ),
            "tone": (
                "persuasive, warm, concise; say what they already own and how the "
                "brand SKU completes the look (not a similar substitute)."
            ),
        },
    }

    system = (
        "You are SuffAI Style Assistant. Build outfit combinations that COMPLETE a look. "
        "Each look MUST use 1–2 wardrobe garment_ids and exactly 1 brand sku_uuid from the JSON. "
        "NEVER pair the same garment category (e.g. shirt with shirt, jeans with jeans). "
        "Pair complementary pieces (shirt+jeans, jeans+jacket, tee+overshirt). "
        "Do not invent ids. Return ONLY a JSON array of objects with keys: "
        "own_garment_ids (array), sku_uuid, title, description, why_it_works."
    )
    prompt = (
        f"{system}\n\nContext JSON:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"Return up to {limit} looks as a JSON array."
    )

    looks_raw: list[dict[str, Any]] = []
    try:
        text = _call_vertex_text(prompt)
        parsed = _extract_json(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("looks"), list):
            parsed = parsed["looks"]
        if not isinstance(parsed, list):
            raise ValueError("expected JSON array")
        looks_raw = [x for x in parsed if isinstance(x, dict)]
    except Exception as exc:
        log.warning("Vertex outfit copy failed; using templates: %s", exc)
        looks_raw = []

    looks: list[dict[str, Any]] = []
    used_skus: set[str] = set()

    def _materialize(own_ids: list[str], sku_uuid: str, title: str, desc: str, why: str) -> Optional[dict[str, Any]]:
        if sku_uuid not in sku_by_id:
            return None
        sku_row = sku_by_id[sku_uuid]
        sku_cat = sku_row["sku"].get("category_bucket") or "unknown"
        own_ids = [g for g in own_ids if g in wardrobe_by_id][:2]
        # Drop same-category wardrobe picks (LLM may ignore rules)
        own_ids = [
            g
            for g in own_ids
            if categories_complementary(
                sku_cat, wardrobe_by_id[g].get("category_bucket") or "unknown"
            )
        ]
        if not own_ids:
            own_ids = [
                g
                for g in (sku_row.get("own_garment_ids") or [])
                if g in wardrobe_by_id
                and categories_complementary(
                    sku_cat, wardrobe_by_id[g].get("category_bucket") or "unknown"
                )
            ][:2]
        if not own_ids:
            for w in wardrobe:
                if categories_complementary(
                    sku_cat, w.get("category_bucket") or "unknown"
                ):
                    own_ids = [w["garment_id"]]
                    break
        if not own_ids:
            return None
        if sku_uuid in used_skus:
            return None
        used_skus.add(sku_uuid)
        rank_row = sku_row
        own = []
        for gid in own_ids:
            w = wardrobe_by_id[gid]
            own.append(
                {
                    "garment_id": gid,
                    "gcs_bucket": w.get("gcs_bucket"),
                    "gcs_object_key": w.get("gcs_object_key"),
                    "label": w.get("label"),
                    "brand_name": w.get("brand_name"),
                    "category_bucket": w.get("category_bucket"),
                    "image_url": w.get("image_url"),
                }
            )
        sku = rank_row["sku"]
        return {
            "score": rank_row["score"],
            "sim_fit": rank_row["sim_fit"],
            "attr_fit": rank_row["attr_fit"],
            "own": own,
            "buy": sku_buy_fields(sku),
            "title": (title or "Complete the look").strip()[:120],
            "description": (desc or "").strip()[:500],
            "why_it_works": (why or "").strip()[:400],
        }

    for raw in looks_raw:
        own_ids = raw.get("own_garment_ids") or raw.get("own") or []
        if isinstance(own_ids, str):
            own_ids = [own_ids]
        if not isinstance(own_ids, list):
            own_ids = []
        own_ids = [str(x) for x in own_ids]
        sku_uuid = str(raw.get("sku_uuid") or "")
        look = _materialize(
            own_ids,
            sku_uuid,
            str(raw.get("title") or ""),
            str(raw.get("description") or ""),
            str(raw.get("why_it_works") or ""),
        )
        if look:
            looks.append(look)
        if len(looks) >= limit:
            break

    # Template fallback / fill remaining slots from ranking
    for r in ranked:
        if len(looks) >= limit:
            break
        sku = r["sku"]
        own_ids = list(r["own_garment_ids"] or [])
        own_labels = [
            wardrobe_by_id[g].get("label") or "your piece"
            for g in own_ids
            if g in wardrobe_by_id
        ]
        own_phrase = " and ".join(own_labels) if own_labels else "your wardrobe piece"
        sku_label = sku.get("label") or "this piece"
        color = (sku.get("attributes") or {}).get("color")
        color_bit = f" in {color}" if color else ""
        sku_cat = sku.get("category_bucket") or "piece"
        own_cats = ", ".join(
            str(c) for c in (r.get("own_categories") or []) if c
        ) or "wardrobe"
        title = f"Complete your look with {brand_name}"
        desc = (
            f"You already have {own_phrase} — add {sku_label}{color_bit} from "
            f"{brand_name} to complete the outfit (not another {own_cats})."
        )
        why = (
            f"Complementary pairing ({own_cats} + {sku_cat}). "
            f"Score {r['score']} from fit ({r['sim_fit']}) and style ({r['attr_fit']})."
        )
        look = _materialize(own_ids, sku["sku_uuid"], title, desc, why)
        if look:
            looks.append(look)

    for i, look in enumerate(looks, start=1):
        look["rank"] = i
    return looks


def _call_vertex_text(prompt: str) -> str:
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
    location = os.getenv("VERTEX_LOCATION", "asia-south1")
    model_name = os.getenv("VERTEX_CHAT_MODEL", "gemini-2.0-flash")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")

    import vertexai  # type: ignore
    from vertexai.generative_models import GenerativeModel  # type: ignore

    vertexai.init(project=project, location=location)
    model = GenerativeModel(model_name)
    resp = model.generate_content(prompt)
    text = getattr(resp, "text", None) or ""
    if not text and getattr(resp, "candidates", None):
        parts = []
        for c in resp.candidates:
            content = getattr(c, "content", None)
            for p in getattr(content, "parts", []) or []:
                t = getattr(p, "text", None)
                if t:
                    parts.append(t)
        text = "\n".join(parts)
    if not text.strip():
        raise RuntimeError("empty model response")
    return text.strip()


def recommend_looks(
    *,
    user_uuid: str,
    brand_name: str,
    limit: int = 6,
    campaign_id: Optional[str] = None,
    use_vertex: bool = True,
) -> dict[str, Any]:
    wardrobe = load_user_wardrobe_rich(user_uuid)
    if not wardrobe:
        raise LookupError(f"No wardrobe items for user_uuid={user_uuid}")

    if campaign_id:
        skus = list_campaign_skus_rich(campaign_id)
    else:
        skus = list_brand_skus(brand_name)
    if not skus:
        raise LookupError(
            f"No brand SKUs for brand_name={brand_name}"
            + (f" campaign={campaign_id}" if campaign_id else "")
        )

    attach_image_urls(wardrobe)
    attach_image_urls(skus)

    ranked_complete = rank_skus_for_user(wardrobe, skus, limit=max(limit, 8))
    # Same SKU may appear in both sections with different framing
    # (complete vs complementary own piece vs similar to same-category own piece).
    ranked_similar = rank_similar_skus_for_user(
        wardrobe,
        skus,
        limit=max(limit, 8),
    )

    if not ranked_complete and not ranked_similar:
        raise LookupError(
            "Could not rank any brand SKUs for this wardrobe "
            "(need complementary and/or same-category matches)"
        )

    complete_outfit = (
        (
            build_llm_outfits(
                wardrobe=wardrobe,
                ranked=ranked_complete,
                brand_name=brand_name,
                user_uuid=user_uuid,
                limit=limit,
            )
            if use_vertex
            else _heuristic_complete_outfits(
                wardrobe=wardrobe,
                ranked=ranked_complete,
                brand_name=brand_name,
                limit=limit,
            )
        )
        if ranked_complete
        else []
    )
    similar_likes = build_similar_likes(
        wardrobe=wardrobe,
        ranked=ranked_similar,
        brand_name=brand_name,
        limit=limit,
    )

    top_score = None
    if ranked_complete:
        top_score = float(ranked_complete[0].get("score") or 0)
    elif ranked_similar:
        top_score = float(ranked_similar[0].get("score") or 0)

    return {
        "ok": True,
        "user_uuid": user_uuid,
        "brand_name": brand_name,
        "wardrobe_count": len(wardrobe),
        "sku_count": len(skus),
        "fit_score": top_score,
        # Primary sections for the Style Assistant UI
        "complete_outfit": complete_outfit,
        "similar_likes": similar_likes,
        # Back-compat alias for complete_outfit
        "looks": complete_outfit,
        "wardrobe": [
            {
                "garment_id": w["garment_id"],
                "label": w.get("label"),
                "brand_name": w.get("brand_name"),
                "category_bucket": w.get("category_bucket"),
                "gcs_object_key": w.get("gcs_object_key"),
                "image_url": w.get("image_url"),
            }
            for w in wardrobe
        ],
    }


def _heuristic_complete_outfits(
    *,
    wardrobe: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    brand_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Offline-friendly looks when Vertex is disabled during campaign materialize."""
    looks: list[dict[str, Any]] = []
    for i, r in enumerate(ranked[:limit], start=1):
        sku = r.get("sku") or {}
        own_ids = r.get("own_garment_ids") or []
        own_items = [w for w in wardrobe if w.get("garment_id") in own_ids][:2]
        looks.append(
            {
                "rank": i,
                "title": f"Look with {sku.get('label') or brand_name}",
                "description": (
                    f"Pairs your wardrobe with {brand_name} "
                    f"({sku.get('label') or 'SKU'}). Score {r.get('score')}."
                ),
                "why": "Matched from your wardrobe style preferences.",
                "score": r.get("score"),
                "sku": sku_buy_fields(sku),
                "own_pieces": [
                    {
                        "garment_id": w.get("garment_id"),
                        "label": w.get("label"),
                        "image_url": w.get("image_url"),
                        "category_bucket": w.get("category_bucket"),
                    }
                    for w in own_items
                ],
            }
        )
    return looks


def materialize_campaign_recommendations(campaign_id: str) -> dict[str, Any]:
    """On Start: eligible users × campaign SKUs → campaign_user_recommendations."""
    campaign = brand_camp.get_campaign(campaign_id)
    if not campaign:
        raise LookupError(f"campaign not found: {campaign_id}")
    brand_name = campaign["brand_name"]
    brand_camp.ensure_campaign_title(campaign_id, brand_name)

    users = brand_camp.eligible_user_uuids_for_campaign(campaign)
    brand_camp.set_campaign_impact(campaign_id, eligible_users=len(users))

    use_vertex = os.getenv("ENABLE_VERTEX", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    computed = 0
    errors = 0
    for user_uuid in users:
        try:
            payload = recommend_looks(
                user_uuid=user_uuid,
                brand_name=brand_name,
                limit=6,
                campaign_id=campaign_id,
                use_vertex=use_vertex,
            )
            # Strip internal campaign identity from stored client payload
            client_payload = {
                "ok": True,
                "brand_name": brand_name,
                "wardrobe_count": payload.get("wardrobe_count"),
                "sku_count": payload.get("sku_count"),
                "complete_outfit": payload.get("complete_outfit") or [],
                "similar_likes": payload.get("similar_likes") or [],
                "looks": payload.get("looks") or [],
                "wardrobe": payload.get("wardrobe") or [],
            }
            brand_camp.upsert_user_recommendation(
                campaign_id=campaign_id,
                user_uuid=user_uuid,
                brand_name=brand_name,
                fit_score=payload.get("fit_score"),
                payload=client_payload,
            )
            computed += 1
        except Exception as exc:
            errors += 1
            log.warning(
                "campaign materialize skip user=%s campaign=%s err=%s",
                user_uuid,
                campaign_id,
                exc,
            )

    brand_camp.set_campaign_impact(campaign_id, recommendations_computed=computed)
    return {
        "campaign_id": campaign_id,
        "eligible_users": len(users),
        "recommendations_computed": computed,
        "errors": errors,
    }


def customer_style_feed(user_uuid: str, *, limit: int = 12) -> dict[str, Any]:
    """
    Style feed for a customer from started 1P campaigns.
    Response deliberately omits campaign_id for client-facing cards.
    """
    rows = brand_camp.list_started_recs_for_user(user_uuid, limit=limit)
    served_campaigns: set[str] = set()
    sections: list[dict[str, Any]] = []
    wardrobe: list[dict[str, Any]] = []

    for row in rows:
        served_campaigns.add(row["campaign_id"])
        payload = row.get("payload") or {}
        if not wardrobe and payload.get("wardrobe"):
            wardrobe = list(payload.get("wardrobe") or [])
        sections.append(
            {
                "brand_name": row["brand_name"],
                "fit_score": row.get("fit_score"),
                "complete_outfit": payload.get("complete_outfit") or payload.get("looks") or [],
                "similar_likes": payload.get("similar_likes") or [],
            }
        )

    for cid in served_campaigns:
        brand_camp.set_campaign_impact(cid, bump_style_feed_serves=1)

    # Flatten for UI that expects a single recommend-shaped payload
    complete: list[dict[str, Any]] = []
    similar: list[dict[str, Any]] = []
    for sec in sections:
        for look in sec.get("complete_outfit") or []:
            complete.append(look)
        for look in sec.get("similar_likes") or []:
            similar.append(look)

    return {
        "ok": True,
        "user_uuid": user_uuid,
        "source": "style_feed",
        "section_count": len(sections),
        "complete_outfit": complete,
        "similar_likes": similar,
        "looks": complete,
        "wardrobe": wardrobe,
        "brands": [s["brand_name"] for s in sections],
    }
