"""Read-only SKU↔PIN unique-garment similarity (no wardrobe writes)."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from similarity import db
from similarity.embed import cosine_similarity, embed_image
from similarity.gcs_io import read_image
from similarity.settings import settings

log = logging.getLogger("similarity.compare_pins")


def _freq_weighted_fit(
    query: List[float], candidates: List[dict[str, Any]], topk: int
) -> Dict[str, Any]:
    if not candidates:
        return {
            "pin_fit": 0.0,
            "candidate_count": 0,
            "total_observations": 0,
            "top_matches": [],
        }

    scored = []
    weight_sum = 0.0
    weighted = 0.0
    total_obs = 0
    for c in candidates:
        emb = c.get("embedding") or []
        if not emb:
            continue
        sim = cosine_similarity(query, emb)
        obs = int(c.get("observation_count") or 0)
        total_obs += obs
        w = math.log1p(max(obs, 0))
        if w <= 0:
            w = 1e-6
        weight_sum += w
        weighted += sim * w
        scored.append(
            {
                "garment_id": c.get("garment_id"),
                "brand_name": c.get("brand_name"),
                "sim": round(sim, 6),
                "observation_count": obs,
            }
        )

    scored.sort(key=lambda x: (x["sim"], x["observation_count"]), reverse=True)
    pin_fit = (weighted / weight_sum) if weight_sum else 0.0
    return {
        "pin_fit": round(float(pin_fit), 6),
        "candidate_count": len(scored),
        "total_observations": total_obs,
        "top_matches": scored[:topk],
    }


def compare_image_to_pins(
    *,
    gcs_object_key: str,
    gcs_bucket: Optional[str],
    pin_codes: List[str],
    topk: int = 5,
    embedding: Optional[List[float]] = None,
) -> Dict[str, Any]:
    pins = [str(p).strip() for p in pin_codes if str(p).strip()]
    if not pins:
        raise ValueError("pin_codes required")
    if not gcs_object_key and not embedding:
        raise ValueError("gcs_object_key or embedding required")

    model_name = settings.VERTEX_EMBED_MODEL
    query = embedding
    if query is None:
        image = read_image(gcs_object_key, bucket=gcs_bucket)
        query, model_name = embed_image(image)
        bucket = image.bucket
        key = image.object_key
    else:
        bucket = gcs_bucket or settings.GCS_IMAGE_BUCKET
        key = gcs_object_key or ""

    by_pin = db.list_garments_for_pins(pins)
    per_pin: Dict[str, Any] = {}
    fits: List[float] = []
    for pin in pins:
        detail = _freq_weighted_fit(query, by_pin.get(pin) or [], topk)
        per_pin[pin] = detail
        fits.append(float(detail["pin_fit"]))

    aggregate = sum(fits) / len(fits) if fits else 0.0
    log.info(
        "compare_pins key=%s pins=%s aggregate=%.4f model=%s",
        key,
        pins,
        aggregate,
        model_name,
    )
    return {
        "ok": True,
        "gcs_bucket": bucket,
        "gcs_object_key": key,
        "pin_codes": pins,
        "aggregate_fit": round(aggregate, 6),
        "per_pin": per_pin,
        "embedding": query,
        "embedding_model": model_name,
        "embedding_dim": len(query) if query else 0,
    }
