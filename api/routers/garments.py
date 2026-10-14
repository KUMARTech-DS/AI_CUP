"""IMG-01 / IMG-02 / DAT-01 — garment upload, extraction jobs, attribute review."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from pydantic import BaseModel, Field

from api.deps import (
    clear_garment_cache,
    find_garment,
    get_customer_index,
    get_garment_store,
    get_garments,
    require_user,
)
from config import settings
from suffai.api.envelope import err, ok
from suffai.gcp_io import publish_extraction_event, signed_upload_url, trigger_worker_http, upload_bytes
from suffai.garments.object_naming import build_garment_object_key
from suffai.garments.store import GarmentStore, coverage_summary
from suffai.metrics.demographics import enrich_garments_with_customers
from suffai.warehouse import repository

router = APIRouter(prefix="/v1/garments", tags=["garments"])


class ConsentBody(BaseModel):
    source_customer_id: str
    purpose: str = Field(
        default="aggregated_b2b_analytics",
        description="operational_processing | aggregated_b2b_analytics | campaign_targeting",
    )
    policy_version: str = "v1"


class InitiateUploadBody(BaseModel):
    source_customer_id: str
    source_order_id: str
    source_order_item_id: str
    source_order_commodity_line_id: str
    physical_item_index: int = 1
    consent_id: str
    order_pin_code: Optional[str] = None
    bill_no: Optional[str] = None
    source_store_id: Optional[str] = None
    source_service_id: Optional[str] = None
    source_catalog_commodity_id: Optional[str] = None
    content_type: str = "image/jpeg"
    capture_source: str = "ops_upload"


class StartExtractionBody(BaseModel):
    image_id: str
    garment_instance_id: Optional[str] = None
    gcs_object_key: Optional[str] = None
    gcs_bucket: Optional[str] = None


class PatchAttributesBody(BaseModel):
    attributes: Dict[str, Any]
    review_status: Optional[str] = None


@router.post("/consent")
def create_consent(body: ConsentBody, user: Dict[str, Any] = Depends(require_user)):
    store = get_garment_store()
    rec = store.create_consent(body.source_customer_id, body.purpose, policy_version=body.policy_version)
    return ok(rec)


@router.post("/consent/{consent_id}/revoke")
def revoke_consent(consent_id: str, user: Dict[str, Any] = Depends(require_user)):
    store = get_garment_store()
    try:
        rec = store.revoke_consent(consent_id)
    except ValueError as exc:
        return err("NOT_FOUND", str(exc))
    return ok(rec)


@router.post("/images:initiate")
def initiate_upload(
    body: InitiateUploadBody,
    user: Dict[str, Any] = Depends(require_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    store = get_garment_store()
    try:
        garment = store.register_garment_instance({
            "source_customer_id": body.source_customer_id,
            "source_order_id": body.source_order_id,
            "source_order_item_id": body.source_order_item_id,
            "source_order_commodity_line_id": body.source_order_commodity_line_id,
            "physical_item_index": body.physical_item_index,
            "consent_id": body.consent_id,
            "order_pin_code": body.order_pin_code,
            "bill_no": body.bill_no,
            "source_store_id": body.source_store_id,
            "source_service_id": body.source_service_id,
            "source_catalog_commodity_id": body.source_catalog_commodity_id,
            "review_status": "pending_upload",
        })
    except ValueError as exc:
        return err("INVALID_FILTER", str(exc))

    try:
        object_key = build_garment_object_key(
            body.source_customer_id,
            body.source_order_id,
            body.source_order_item_id,
            content_type=body.content_type,
        )
    except ValueError as exc:
        return err("INVALID_FILTER", str(exc))
    image = store.register_image(
        garment_instance_id=garment["garment_instance_id"],
        consent_id=body.consent_id,
        gcs_object_key=object_key,
        content_type=body.content_type,
        capture_source=body.capture_source,
    )
    upload = signed_upload_url(object_key, content_type=body.content_type)
    key = idempotency_key or f"init-{image['image_id']}"
    job = store.create_extraction_job(image_id=image["image_id"], idempotency_key=key, status="PENDING_UPLOAD")

    return ok({
        "garment_instance": garment,
        "image": image,
        "extraction_job": job,
        "upload": upload,
        "idempotency_key": key,
    })


@router.post("/images/upload-local")
async def upload_local(
    file: UploadFile = File(...),
    gcs_object_key: str = Form(...),
    image_id: Optional[str] = Form(None),
    user: Dict[str, Any] = Depends(require_user),
):
    """Fallback when a signed URL isn't available: proxies the upload into GCS (local disk only in dev)."""
    data = await file.read()
    if not data:
        return err("INVALID_FILTER", "Empty file")
    bucket = settings.GCS_IMAGE_BUCKET or settings.GCS_BUCKET
    if bucket:
        dest = upload_bytes(bucket, gcs_object_key, data, file.content_type or "image/jpeg")
    else:
        local = settings.IMAGE_STORE_DIR / gcs_object_key
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        dest = str(local)
    sha = hashlib.sha256(data).hexdigest()

    store = get_garment_store()
    # Update image checksum if known
    images = store._load("garment_images")
    for img in images:
        if img.get("gcs_object_key") == gcs_object_key or (image_id and img.get("image_id") == image_id):
            img["sha256"] = sha
            img["stored_at"] = dest
            break
    store._save("garment_images", images)

    # Mark related jobs UPLOADED
    for job in store._load("extraction_jobs"):
        if job.get("image_id") == image_id or (
            image_id is None and any(
                i.get("gcs_object_key") == gcs_object_key and i.get("image_id") == job.get("image_id")
                for i in images
            )
        ):
            if job.get("status") == "PENDING_UPLOAD":
                store.update_extraction_job(job["job_id"], status="UPLOADED")

    return ok({"gcs_object_key": gcs_object_key, "sha256": sha, "bytes": len(data), "stored": dest})


@router.post("/extractions")
def start_extraction(
    body: StartExtractionBody,
    user: Dict[str, Any] = Depends(require_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    store = get_garment_store()
    images = store._load("garment_images")
    image = next((i for i in images if i.get("image_id") == body.image_id), None)
    if not image:
        return err("NOT_FOUND", "image_id not found")

    object_key = body.gcs_object_key or image.get("gcs_object_key")
    garment_id = body.garment_instance_id or image.get("garment_instance_id")
    garment = store.get_garment(garment_id) if garment_id else None
    key = idempotency_key or f"extract-{body.image_id}"
    job = store.create_extraction_job(image_id=body.image_id, idempotency_key=key, status="QUEUED")
    store.update_extraction_job(job["job_id"], status="QUEUED")

    payload = {
        "image_id": body.image_id,
        "garment_instance_id": garment_id,
        "gcs_object_key": object_key,
        "gcs_bucket": body.gcs_bucket or settings.GCS_IMAGE_BUCKET or settings.GCS_BUCKET,
        "idempotency_key": key,
        "target_type": "garment",
        "user_uuid": (garment or {}).get("source_customer_id"),
        "order_uuid": (garment or {}).get("source_order_id"),
        "item_uuid": (garment or {}).get("source_order_item_id"),
        "source_customer_id": (garment or {}).get("source_customer_id"),
        "source_order_id": (garment or {}).get("source_order_id"),
        "source_order_item_id": (garment or {}).get("source_order_item_id"),
    }
    pub = publish_extraction_event(payload)
    worker = trigger_worker_http(payload)

    # Offline / demo path: if neither pubsub nor worker, apply stub attributes for review
    if not pub.get("published") and not worker.get("triggered"):
        store.apply_attributes(
            garment_id,
            {
                "commodity_type": "unknown",
                "material_type": "unknown",
                "primary_color": "unknown",
                "brand_name_raw": "unknown",
                "gender": "unknown",
                "attribute_evidence": {
                    "note": {"source": "api_stub", "confidence": 0.0, "reason": "worker/pubsub unavailable"}
                },
            },
            actor_user_id=user.get("sub") or "system",
            review_status="needs_review",
        )
        store.update_extraction_job(
            job["job_id"],
            status="NEEDS_REVIEW",
            raw_result={"mode": "api_stub"},
            completed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        )
        job = store.get_extraction_job(job["job_id"])

    return ok({
        "job": job,
        "pubsub": pub,
        "worker": worker,
        "payload": payload,
    }, coverage={"JOB_PENDING": True})


@router.get("/extractions/{job_id}")
def get_extraction(job_id: str, user: Dict[str, Any] = Depends(require_user)):
    store = get_garment_store()
    job = store.get_extraction_job(job_id)
    if not job:
        return err("NOT_FOUND", "job not found")
    code = "JOB_PENDING" if job.get("status") in {"PENDING_UPLOAD", "UPLOADED", "QUEUED", "PROCESSING"} else None
    return ok(job, coverage={"pending": bool(code)})


@router.get("")
def list_garments(
    review_status: Optional[str] = None,
    user: Dict[str, Any] = Depends(require_user),
):
    all_rows = get_garments()
    rows = all_rows
    if review_status:
        rows = [g for g in rows if g.get("review_status") == review_status]
    # Never return PII beyond source ids already stored for ops
    rows = enrich_garments_with_customers(rows[:200], get_customer_index())
    safe = []
    for g in rows:
        safe.append({
            "garment_instance_id": g.get("garment_instance_id"),
            "source_customer_id": g.get("source_customer_id"),
            "source_order_id": g.get("source_order_id"),
            "source_order_item_id": g.get("source_order_item_id"),
            "gcs_uri": g.get("gcs_uri"),
            "order_pin_code": g.get("order_pin_code"),
            "commodity_type": g.get("commodity_type"),
            "material_type": g.get("material_type"),
            "primary_color": g.get("primary_color"),
            "size_normalized": g.get("size_normalized"),
            "brand_name_raw": g.get("brand_name_raw"),
            "gender": g.get("gender"),
            "customer_gender": g.get("customer_gender"),
            "customer_age_band": g.get("customer_age_band"),
            "customer_pin": g.get("customer_pin"),
            "customer_device_type": g.get("customer_device_type"),
            "review_status": g.get("review_status"),
            "attribute_evidence": g.get("attribute_evidence"),
            "updated_at": g.get("updated_at"),
        })
    return ok({"garments": safe, "coverage": coverage_summary(all_rows)})


@router.patch("/{garment_id}/attributes")
def patch_attributes(
    garment_id: str,
    body: PatchAttributesBody,
    user: Dict[str, Any] = Depends(require_user),
):
    actor = str(user.get("sub") or user.get("email") or "brand_user")
    if repository.enabled():
        garment = find_garment(garment_id)
        if garment and garment.get("source") == "bigquery":
            try:
                row = repository.append_review_row(
                    garment, body.attributes, review_status=body.review_status, actor=actor
                )
            except Exception as exc:
                return err("INSUFFICIENT_DATA", f"Review not saved to BigQuery: {exc}")
            clear_garment_cache()
            return ok({**garment, **body.attributes, "review_status": row["review_status"]})
    store = get_garment_store()
    try:
        updated = store.apply_attributes(
            garment_id,
            body.attributes,
            actor_user_id=actor,
            review_status=body.review_status,
        )
    except ValueError as exc:
        return err("NOT_FOUND", str(exc))
    return ok(updated)
