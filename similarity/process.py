"""Core similarity pipeline: embed → match user → optional global → counters."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from similarity import bq_sync, db
from similarity.embed import cosine_similarity, embed_image
from similarity.gcs_io import parse_path_context, read_image
from similarity.settings import settings

log = logging.getLogger("similarity.process")

PIN_RE = re.compile(r"^\d{6}$")


def _best_match(
    query: List[float], candidates: List[Tuple[str, List[float], Optional[str]]]
) -> Tuple[Optional[str], float]:
    best_id: Optional[str] = None
    best_score = -1.0
    for gid, emb, _brand in candidates:
        score = cosine_similarity(query, emb)
        if score > best_score:
            best_score = score
            best_id = gid
    return best_id, best_score


def resolve_context(payload: Dict[str, Any], gcs_metadata: Dict[str, str], object_key: str) -> Dict[str, Optional[str]]:
    """Prefer GCS metadata, then Pub/Sub/payload attrs, then path."""
    path_ctx = parse_path_context(object_key)
    attrs = payload.get("attributes") if isinstance(payload.get("attributes"), dict) else {}

    def pick(*keys: str) -> Optional[str]:
        for k in keys:
            for source in (gcs_metadata, attrs, payload, path_ctx):
                if not isinstance(source, dict):
                    continue
                # case-insensitive for metadata
                for sk, sv in source.items():
                    if str(sk).lower() == k.lower() and sv not in (None, ""):
                        return str(sv).strip()
        return None

    user_uuid = pick("user_uuid", "userUuid", "user_id")
    pin_code = pick("pin_code", "pinCode", "order_pin_code")
    brand = pick("brand", "brand_name", "brandName")
    return {"user_uuid": user_uuid, "pin_code": pin_code, "brand_name": brand}


def process_upload(payload: Dict[str, Any]) -> Dict[str, Any]:
    object_key = (
        payload.get("gcs_object_key")
        or payload.get("object_key")
        or payload.get("name")
        or ""
    ).strip()
    bucket = payload.get("gcs_bucket") or payload.get("bucket")
    idem = (
        payload.get("idempotency_key")
        or payload.get("message_id")
        or f"sim:{object_key}"
    )

    if not object_key:
        raise ValueError("gcs_object_key is required")

    if settings.DATABASE_URL and db.event_already_processed(str(idem)):
        log.info("idempotent skip key=%s", idem)
        return {"status": "already_processed", "idempotency_key": idem}

    image = read_image(object_key, bucket=bucket if isinstance(bucket, str) else None)
    ctx = resolve_context(payload, image.metadata, image.object_key)
    user_uuid = ctx["user_uuid"]
    pin_code = ctx["pin_code"]
    brand_name = ctx["brand_name"]

    if not user_uuid or not pin_code:
        msg = "user_uuid and pin_code required (GCS metadata, Pub/Sub attrs, or path garments/{user}/{pin}/…)"
        log.error("missing_context key=%s user=%s pin=%s", object_key, user_uuid, pin_code)
        if settings.DATABASE_URL:
            db.record_event(
                gcs_bucket=image.bucket,
                gcs_object_key=image.object_key,
                user_uuid=user_uuid,
                pin_code=pin_code,
                brand_name=brand_name,
                outcome="skipped_missing_context",
                error_message=msg,
                idempotency_key=str(idem),
            )
        return {
            "status": "skipped_missing_context",
            "error": msg,
            "gcs_object_key": image.object_key,
        }

    if not PIN_RE.match(pin_code):
        raise ValueError(f"pin_code must be 6 digits, got: {pin_code!r}")

    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")

    embedding, model = embed_image(image)
    threshold = settings.SIMILARITY_THRESHOLD

    def _after(outcome: str, garment_id: str, sim_score: Optional[float]) -> None:
        bq_sync.sync_observation(
            garment_id=garment_id,
            user_uuid=user_uuid,
            pin_code=pin_code,
            brand_name=brand_name,
            outcome=outcome,
            gcs_object_key=image.object_key,
            similarity_score=sim_score,
        )

    # 1) Match against this user's wardrobe
    user_cands = db.load_user_embeddings(user_uuid)
    matched_id, score = _best_match(embedding, user_cands)
    if matched_id and score >= threshold:
        db.increment_pin_and_total(garment_id=matched_id, pin_code=pin_code)
        db.record_event(
            gcs_bucket=image.bucket,
            gcs_object_key=image.object_key,
            user_uuid=user_uuid,
            pin_code=pin_code,
            brand_name=brand_name,
            outcome="user_match",
            matched_garment_id=matched_id,
            similarity_score=score,
            idempotency_key=str(idem),
        )
        _after("user_match", matched_id, score)
        log.info(
            "user_match user=%s garment=%s score=%.4f pin=%s",
            user_uuid,
            matched_id,
            score,
            pin_code,
        )
        return {
            "status": "user_match",
            "garment_id": matched_id,
            "similarity_score": score,
            "added_to_user_wardrobe": False,
            "pin_count_incremented": True,
        }

    # 2) No user match → try global nearest neighbor
    global_cands = db.load_global_embeddings(
        brand_name=brand_name, limit=settings.GLOBAL_CANDIDATE_LIMIT
    )
    # exclude already-compared user garments from global? fine to include; won't link twice due to UNIQUE
    g_id, g_score = _best_match(embedding, global_cands)
    if g_id and g_score >= threshold:
        db.link_user_to_garment(
            user_uuid=user_uuid,
            garment_id=g_id,
            object_key=image.object_key,
            pin_code=pin_code,
        )
        db.increment_pin_and_total(garment_id=g_id, pin_code=pin_code)
        db.record_event(
            gcs_bucket=image.bucket,
            gcs_object_key=image.object_key,
            user_uuid=user_uuid,
            pin_code=pin_code,
            brand_name=brand_name,
            outcome="global_match_new_user_link",
            matched_garment_id=g_id,
            similarity_score=g_score,
            idempotency_key=str(idem),
        )
        _after("global_match_new_user_link", g_id, g_score)
        log.info(
            "global_match user=%s garment=%s score=%.4f",
            user_uuid,
            g_id,
            g_score,
        )
        return {
            "status": "global_match_new_user_link",
            "garment_id": g_id,
            "similarity_score": g_score,
            "added_to_user_wardrobe": True,
            "pin_count_incremented": True,
        }

    # 3) Create new unique garment + wardrobe row
    new_id = db.create_unique_garment(
        bucket=image.bucket,
        object_key=image.object_key,
        embedding=embedding,
        model=model,
        user_uuid=user_uuid,
        pin_code=pin_code,
        brand_name=brand_name,
    )
    db.record_event(
        gcs_bucket=image.bucket,
        gcs_object_key=image.object_key,
        user_uuid=user_uuid,
        pin_code=pin_code,
        brand_name=brand_name,
        outcome="created_new",
        matched_garment_id=new_id,
        similarity_score=None,
        idempotency_key=str(idem),
    )
    _after("created_new", new_id, None)
    log.info("created_new user=%s garment=%s pin=%s brand=%s", user_uuid, new_id, pin_code, brand_name)
    return {
        "status": "created_new",
        "garment_id": new_id,
        "similarity_score": None,
        "added_to_user_wardrobe": True,
        "pin_count_incremented": True,
        "embedding_model": model,
    }
