"""
Brand SKU vs. garments observed in PINs (read-only).

Used by Command Center Ads Lab ranking. Never writes unique_garments / user_wardrobe /
garment_pin_counts / events: brand catalogue images are not customer observations.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from similarity import db
from similarity.embed import embed_image
from similarity.gcs_io import read_image
from similarity.settings import settings

log = logging.getLogger("similarity.compare")

PIN_RE = re.compile(r"^\d{6}$")

# (pin, garment_id, observation_count, unit_embedding)
Candidate = Tuple[str, str, int, List[float]]
CandidateLoader = Callable[[Sequence[str], int], Tuple[list, Dict[str, int]]]

_cache_lock = threading.Lock()
_cache: Dict[tuple, Tuple[float, List[Candidate], Dict[str, int]]] = {}


def normalize_pins(pin_codes: Sequence[Any]) -> List[str]:
    pins: List[str] = []
    for p in pin_codes or []:
        s = str(p or "").strip()
        if not s:
            continue
        if not PIN_RE.match(s):
            raise ValueError(f"pin_code must be 6 digits, got: {s!r}")
        if s not in pins:
            pins.append(s)
    if not pins:
        raise ValueError("pin_codes required (one or more)")
    if len(pins) > settings.COMPARE_MAX_PINS:
        raise ValueError(f"at most {settings.COMPARE_MAX_PINS} pin_codes")
    return pins


def _unit(vec: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return []
    return [x / norm for x in vec]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def load_candidates(
    pins: Sequence[str], model: str, dim: int, *, loader: Optional[CandidateLoader] = None
) -> Tuple[List[Candidate], Dict[str, int]]:
    """Candidates for these PINs with the same embedding model and size; cached per PIN set."""
    limit = max(1, settings.COMPARE_PIN_CANDIDATE_LIMIT)
    key = (tuple(sorted(pins)), model, dim, limit)
    ttl = settings.COMPARE_CACHE_SEC
    now = time.monotonic()
    if ttl > 0:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and hit[0] > now:
                return hit[1], hit[2]
    rows, totals = (loader or (lambda p, n: db.load_pin_candidates(p, per_pin_limit=n)))(list(pins), limit)
    cands: List[Candidate] = []
    skipped = 0
    for pin, gid, count, emb, emb_model in rows:
        if len(emb) != dim or (emb_model and emb_model != model):
            skipped += 1
            continue
        unit = _unit(emb)
        if unit:
            cands.append((pin, gid, int(count), unit))
    if skipped:
        log.warning("compare candidates skipped (model/dim mismatch) n=%s model=%s dim=%s", skipped, model, dim)
    if ttl > 0:
        with _cache_lock:
            if len(_cache) > 64:
                _cache.clear()
            _cache[key] = (now + ttl, cands, totals)
    return cands, totals


def score_pins(
    query: Sequence[float],
    pins: Sequence[str],
    candidates: Sequence[Candidate],
    totals: Dict[str, int],
    *,
    topk: int,
) -> Dict[str, Any]:
    """
    pin_fit: observation-weighted mean cosine (floored at 0) of the top-k most similar garments.
    aggregate_fit: PIN fits weighted by each PIN's total observations (plain mean if none).
    """
    q = _unit(query)
    by_pin: Dict[str, List[Tuple[float, str, int]]] = {p: [] for p in pins}
    for pin, gid, count, unit in candidates:
        if pin in by_pin:
            by_pin[pin].append((max(0.0, _dot(q, unit)) if q else 0.0, gid, count))
    per_pin: Dict[str, Any] = {}
    for pin in pins:
        scored = sorted(by_pin[pin], key=lambda t: t[0], reverse=True)[:topk]
        weight = sum(max(1, c) for _, _, c in scored)
        fit = sum(s * max(1, c) for s, _, c in scored) / weight if weight else 0.0
        per_pin[pin] = {
            "pin_fit": round(fit, 4),
            "total_observations": int(totals.get(pin, 0)),
            "candidates": len(by_pin[pin]),
            "top_matches": [
                {"garment_id": gid, "similarity": round(s, 4), "observation_count": c}
                for s, gid, c in scored
            ],
        }
    weighted = [(d["pin_fit"], d["total_observations"]) for d in per_pin.values() if d["candidates"]]
    total_w = sum(w for _, w in weighted)
    if total_w > 0:
        aggregate = sum(f * w for f, w in weighted) / total_w
    elif weighted:
        aggregate = sum(f for f, _ in weighted) / len(weighted)
    else:
        aggregate = 0.0
    return {"aggregate_fit": round(aggregate, 4), "per_pin": per_pin}


def compare_pins(
    *,
    gcs_object_key: str,
    gcs_bucket: Optional[str],
    pin_codes: Sequence[Any],
    topk: int = 5,
    include_embedding: bool = True,
) -> Dict[str, Any]:
    pins = normalize_pins(pin_codes)
    topk = max(1, min(50, int(topk or 5)))
    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    t0 = time.monotonic()
    image = read_image(gcs_object_key, bucket=gcs_bucket)
    embedding, model = embed_image(image)
    candidates, totals = load_candidates(pins, model, len(embedding))
    out = score_pins(embedding, pins, candidates, totals, topk=topk)
    log.info(
        "compare_pins key=%s pins=%s candidates=%s fit=%.4f ms=%d",
        image.object_key, len(pins), len(candidates), out["aggregate_fit"],
        int((time.monotonic() - t0) * 1000),
    )
    out.update(
        {
            "status": "ok",
            "gcs_object_key": image.object_key,
            "pin_codes": pins,
            "topk": topk,
            "embedding_model": model,
            "embedding_dim": len(embedding),
        }
    )
    if include_embedding:
        out["embedding"] = list(embedding)
    return out
