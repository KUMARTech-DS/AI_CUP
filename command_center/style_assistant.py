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


def load_user_wardrobe_rich(user_uuid: str, *, limit: int = 80) -> list[dict[str, Any]]:
    with brand_camp.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ug.garment_id::text,
                       ug.brand_name,
                       ug.representative_gcs_bucket,
                       ug.representative_gcs_object_key,
                       ug.embedding,
                       uw.pin_code_at_first_sight,
                       ug.observation_count_total
                FROM user_wardrobe uw
                JOIN unique_garments ug ON ug.garment_id = uw.garment_id
                WHERE uw.user_uuid = %s
                ORDER BY ug.updated_at DESC
                LIMIT %s
                """,
                (user_uuid, limit),
            )
            rows = []
            for r in cur.fetchall():
                emb = _parse_embedding(r[4])
                brand = r[1]
                rows.append(
                    {
                        "garment_id": r[0],
                        "brand_name": brand,
                        "gcs_bucket": r[2],
                        "gcs_object_key": r[3],
                        "embedding": emb,
                        "pin_code": r[5],
                        "observation_count_total": int(r[6] or 0),
                        "label": brand or "Wardrobe piece",
                        "attributes": {"brand": brand} if brand else {},
                    }
                )
            return rows


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
            out = []
            for r in cur.fetchall():
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
                out.append(
                    {
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
                )
            return out


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


def rank_skus_for_user(
    wardrobe: list[dict[str, Any]],
    skus: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    profile = build_wardrobe_taste_profile(wardrobe)
    wardrobe_with_emb = [w for w in wardrobe if w.get("embedding")]
    ranked: list[dict[str, Any]] = []

    for sku in skus:
        sku_emb = sku.get("embedding")
        best_cos = -1.0
        best_matches: list[tuple[str, float]] = []
        for w in wardrobe_with_emb:
            cos = cosine_similarity(sku_emb or [], w["embedding"] or [])
            best_matches.append((w["garment_id"], cos))
            if cos > best_cos:
                best_cos = cos
        best_matches.sort(key=lambda t: t[1], reverse=True)
        top_own = [gid for gid, _ in best_matches[:2]]

        if sku_emb and wardrobe_with_emb:
            sim = sim_fit_01(best_cos)
            above = sum(1 for _, c in best_matches if c >= COSINE_MATCH_THRESHOLD)
            freq = above / max(1, len(best_matches))
        else:
            # No embeddings: fall back to attribute overlap only
            sim = 0.0
            freq = 0.0
            if not top_own and wardrobe:
                top_own = [wardrobe[0]["garment_id"]]
                if len(wardrobe) > 1:
                    top_own.append(wardrobe[1]["garment_id"])

        a_fit = brand_camp.attr_fit_score(sku.get("attributes") or {}, profile)
        score = brand_camp.blend_rank_score(
            sim_fit=sim, freq_coverage=freq, attr_fit=a_fit
        )
        ranked.append(
            {
                "sku": sku,
                "score": score,
                "sim_fit": round(sim, 4),
                "freq_coverage": round(freq, 4),
                "attr_fit": round(a_fit, 4),
                "best_cosine": round(best_cos, 4) if best_cos > -1 else None,
                "own_garment_ids": top_own,
            }
        )

    ranked.sort(key=lambda r: (-(r["score"] or 0), r["sku"]["sku_uuid"]))
    return ranked[:limit]


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
                "attributes": w.get("attributes") or {},
            }
            for w in wardrobe[:40]
        ],
        "ranked_skus": [
            {
                "sku_uuid": r["sku"]["sku_uuid"],
                "label": r["sku"].get("label"),
                "brand_name": r["sku"].get("brand_name"),
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
            "tone": "persuasive, warm, concise; tell user what they own and why the SKU completes the look",
        },
    }

    system = (
        "You are SuffAI Style Assistant. Build outfit combinations for a shopper. "
        "Each look MUST use 1–2 wardrobe garment_ids and exactly 1 brand sku_uuid from the JSON context. "
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
        own_ids = [g for g in own_ids if g in wardrobe_by_id][:2]
        if not own_ids:
            own_ids = list(sku_by_id[sku_uuid]["own_garment_ids"] or [])[:2]
            own_ids = [g for g in own_ids if g in wardrobe_by_id]
        if not own_ids and wardrobe:
            own_ids = [wardrobe[0]["garment_id"]]
        if not own_ids:
            return None
        if sku_uuid in used_skus:
            return None
        used_skus.add(sku_uuid)
        rank_row = sku_by_id[sku_uuid]
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
                    "image_url": w.get("image_url"),
                }
            )
        sku = rank_row["sku"]
        buy = {
            "sku_uuid": sku_uuid,
            "gcs_bucket": sku.get("gcs_bucket"),
            "gcs_object_key": sku.get("gcs_object_key"),
            "attributes": sku.get("attributes") or {},
            "brand_name": sku.get("brand_name"),
            "label": sku.get("label"),
            "image_url": sku.get("image_url"),
        }
        return {
            "score": rank_row["score"],
            "sim_fit": rank_row["sim_fit"],
            "attr_fit": rank_row["attr_fit"],
            "own": own,
            "buy": buy,
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
        title = f"Pair your look with {brand_name}"
        desc = (
            f"You have {own_phrase} — {sku_label}{color_bit} from {brand_name} "
            f"makes a perfect fit for a polished everyday outfit."
        )
        why = (
            f"Ranked fit score {r['score']} using wardrobe similarity "
            f"({r['sim_fit']}) and style attributes ({r['attr_fit']})."
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
) -> dict[str, Any]:
    wardrobe = load_user_wardrobe_rich(user_uuid)
    if not wardrobe:
        raise LookupError(f"No wardrobe items for user_uuid={user_uuid}")

    skus = list_brand_skus(brand_name)
    if not skus:
        raise LookupError(f"No brand SKUs for brand_name={brand_name}")

    attach_image_urls(wardrobe)
    attach_image_urls(skus)

    ranked = rank_skus_for_user(wardrobe, skus, limit=max(limit, 8))
    if not ranked:
        raise LookupError("Could not rank any SKUs for this wardrobe")

    looks = build_llm_outfits(
        wardrobe=wardrobe,
        ranked=ranked,
        brand_name=brand_name,
        user_uuid=user_uuid,
        limit=limit,
    )

    return {
        "ok": True,
        "user_uuid": user_uuid,
        "brand_name": brand_name,
        "wardrobe_count": len(wardrobe),
        "sku_count": len(skus),
        "wardrobe": [
            {
                "garment_id": w["garment_id"],
                "label": w.get("label"),
                "brand_name": w.get("brand_name"),
                "gcs_object_key": w.get("gcs_object_key"),
                "image_url": w.get("image_url"),
            }
            for w in wardrobe
        ],
        "looks": looks,
    }
