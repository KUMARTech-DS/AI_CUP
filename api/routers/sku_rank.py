"""SKU Ranking API (PRD/ERD v1.2) — rank 1..20 garment images for one PIN; backs the Ads Lab tab."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from api import deps
from api.deps import get_customer_index, get_garment_store, get_normalized, get_rfm, require_user
from config import settings
from suffai import PRIVACY_K
from suffai.api.envelope import err, ok
from suffai.garments.sku_vision import (
    MAX_IMAGE_BYTES,
    MAX_TOTAL_BYTES,
    ImageRejected,
    SkuImage,
    UpstreamUnavailable,
    extract_all,
    validate_image,
)
from suffai.metrics.geo import PIN_AREA_LABELS, geo_active_customers
from suffai.metrics.sku_rank import DEFAULT_LOOKBACK_DAYS, SCORING_VERSION, WEIGHTS, build_context, rank_skus
from suffai.warehouse import repository

log = logging.getLogger("suffai.sku_rank")

router = APIRouter(prefix="/api/v1/sku", tags=["sku-ranking"])

PIN_RE = re.compile(r"^[1-9][0-9]{5}$")
MAX_IMAGES = 20
LOOKBACK_RANGE = (7, 730)


def _fail(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=err(code, message))


def _garment_history() -> List[Dict[str, Any]]:
    if repository.enabled():
        return deps._bq_garments()
    return deps.get_garments()


def _rank(images: List[SkuImage], pincode: str, lookback_days: int) -> Dict[str, Any]:
    attrs = extract_all(images)
    try:
        garments = _garment_history()
    except Exception as exc:
        log.warning("garment history unavailable: %s", type(exc).__name__)
        raise UpstreamUnavailable("BigQuery garment history unavailable") from exc
    norm = get_normalized()
    ctx = build_context(
        pincode=pincode,
        garments=garments,
        lines=norm.get("order_commodity_lines") or [],
        orders=norm.get("orders") or [],
        customers_by_id=get_customer_index(),
        rfm_snapshots=get_rfm(),
        consent_records=get_garment_store()._load("consent_records"),
        lookback_days=lookback_days,
        consent_mode=settings.SKU_RANK_CONSENT_MODE,
        k=PRIVACY_K,
    )
    first_seen: Dict[str, int] = {}
    items = []
    for img in images:
        first_seen.setdefault(img.sha256, img.input_index)
        items.append({
            "input_index": img.input_index,
            "image_id": img.image_id,
            "image_sha256": img.sha256,
            "filename": img.filename,
            "duplicate_of": first_seen[img.sha256] if first_seen[img.sha256] != img.input_index else None,
            "attributes": attrs[img.sha256],
        })
    sources = sorted({str(a.get("source")) for a in attrs.values()})
    return {
        "ranked_results": rank_skus(items, ctx),
        "data_as_of": ctx.data_as_of,
        "area_profile": ctx.area_profile,
        "attribute_source": sources[0] if len(sources) == 1 else sources,
    }


@router.post("/rank")
async def rank(
    request: Request,
    images: Optional[List[UploadFile]] = File(default=None),
    pincode: str = Form(default=""),
    lookback_days: str = Form(default=str(DEFAULT_LOOKBACK_DAYS)),
    user: Dict[str, Any] = Depends(require_user),
):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_TOTAL_BYTES + 1024 * 1024:
        return _fail(413, "PAYLOAD_TOO_LARGE", f"Request exceeds {MAX_TOTAL_BYTES} bytes")
    pincode = (pincode or "").strip()
    if not PIN_RE.match(pincode):
        return _fail(422, "INVALID_PINCODE", "pincode must be a 6-digit Indian PIN code")
    try:
        lookback = int(str(lookback_days).strip())
    except ValueError:
        return _fail(422, "INVALID_LOOKBACK", "lookback_days must be an integer")
    if not LOOKBACK_RANGE[0] <= lookback <= LOOKBACK_RANGE[1]:
        return _fail(422, "INVALID_LOOKBACK", f"lookback_days must be between {LOOKBACK_RANGE[0]} and {LOOKBACK_RANGE[1]}")
    files = [f for f in images or [] if f is not None]
    if not 1 <= len(files) <= MAX_IMAGES:
        return _fail(422, "INVALID_IMAGE_COUNT", f"Provide between 1 and {MAX_IMAGES} images (got {len(files)})")

    validated: List[SkuImage] = []
    total = 0
    try:
        for i, f in enumerate(files):
            data = await f.read(MAX_IMAGE_BYTES + 1)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ImageRejected(413, "PAYLOAD_TOO_LARGE", f"Images exceed {MAX_TOTAL_BYTES} bytes in total")
            validated.append(validate_image(i, f.filename, f.content_type, data))
    except ImageRejected as exc:
        return _fail(exc.status, exc.code, exc.message)

    run_id = str(uuid.uuid4())
    try:
        result = await run_in_threadpool(_rank, validated, pincode, lookback)
    except UpstreamUnavailable as exc:
        return _fail(503, "UPSTREAM_UNAVAILABLE", str(exc))

    tenant_id = str(user.get("tenant_id") or user.get("brand_id") or "")
    get_garment_store().audit(
        "sku_rank_run",
        run_id=run_id,
        tenant_id=tenant_id,
        brand=user.get("brand"),
        pincode=pincode,
        image_count=len(validated),
        scoring_version=SCORING_VERSION,
    )
    log.info("sku_rank run_id=%s tenant=%s pincode=%s images=%s", run_id, tenant_id, pincode, len(validated))
    return ok({
        "run_id": run_id,
        "pincode": pincode,
        "area": PIN_AREA_LABELS.get(pincode, pincode),
        "brand": user.get("brand"),
        "lookback_days": lookback,
        "data_as_of": result["data_as_of"],
        "scoring_version": SCORING_VERSION,
        "weights": WEIGHTS,
        "privacy_k": PRIVACY_K,
        "image_count": len(validated),
        "attribute_source": result["attribute_source"],
        "ranked_results": result["ranked_results"],
        "area_profile": result["area_profile"],
        "disclaimer": (
            "Opportunity is a historical garment-demand proxy for the PIN, not a sales forecast, "
            "predicted CTR or causal uplift. Aggregates under k distinct customers are suppressed."
        ),
    }, as_of=result["data_as_of"])


@router.get("/areas")
def areas(user: Dict[str, Any] = Depends(require_user)):
    """PINs with at least k eligible customers — the Ads Lab target-area picker."""
    rows = [
        {"pincode": r["pin_code"], "area": r["area"], "active_customers": r["active_customers"]}
        for r in geo_active_customers(get_normalized()["orders"], k=PRIVACY_K)
        if r.get("pin_code") and not r.get("suppressed") and PIN_RE.match(str(r["pin_code"]))
    ]
    return ok({"areas": rows[:40], "privacy_k": PRIVACY_K, "max_images": MAX_IMAGES,
               "default_lookback_days": DEFAULT_LOOKBACK_DAYS})
