"""
Garment similarity Cloud Run service (Pub/Sub push + admin APIs).

Independent of suffai-extraction / command_center core logic.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import traceback
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from similarity import db
from similarity.process import process_upload
from similarity.settings import settings

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s [similarity] %(message)s",
)
log = logging.getLogger("similarity")

app = FastAPI(title="Suffai Garment Similarity", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProcessRequest(BaseModel):
    gcs_object_key: str
    gcs_bucket: Optional[str] = None
    user_uuid: Optional[str] = None
    pin_code: Optional[str] = None
    brand: Optional[str] = None
    brand_name: Optional[str] = None
    idempotency_key: Optional[str] = None


class ComparePinsRequest(BaseModel):
    """Brand SKU visual fit vs unique garments in selected PINs (no wardrobe writes)."""

    pin_codes: list[str]
    gcs_object_key: Optional[str] = None
    gcs_bucket: Optional[str] = None
    embedding: Optional[list[float]] = None
    topk: int = 5


def parse_key_object_metadata(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Pub/Sub GCS notifications — same shape as worker/main.py."""
    message = envelope.get("message") or {}
    attributes = message.get("attributes") or {}
    metadata: Dict[str, Any] = {}
    if message.get("data"):
        try:
            metadata = json.loads(base64.b64decode(message["data"]).decode("utf-8"))
            if not isinstance(metadata, dict):
                metadata = {}
        except Exception:
            log.exception("failed to decode pubsub data")
            metadata = {}

    object_key = (
        attributes.get("objectId")
        or metadata.get("name")
        or metadata.get("gcs_object_key")
        or metadata.get("object_key")
    )
    bucket = (
        attributes.get("bucketId")
        or metadata.get("bucket")
        or metadata.get("gcs_bucket")
    )
    if not object_key:
        raise ValueError("gcs_object_key is required")

    merged = {
        **metadata,
        "attributes": attributes,
        "gcs_object_key": object_key,
        "gcs_bucket": bucket,
        "event_type": attributes.get("eventType"),
        "object_generation": attributes.get("objectGeneration"),
    }
    for k in ("user_uuid", "pin_code", "brand", "brand_name"):
        if attributes.get(k) and not merged.get(k):
            merged[k] = attributes[k]
    return merged


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "suffai-similarity",
        "enable_vertex": settings.ENABLE_VERTEX,
        "use_bq": settings.USE_BQ,
        "bq_dataset": settings.BQ_DATASET,
        "project": bool(settings.PROJECT_ID),
        "gcs_bucket": bool(settings.GCS_IMAGE_BUCKET),
        "database_url": bool(settings.DATABASE_URL),
        "similarity_threshold": settings.SIMILARITY_THRESHOLD,
        "vertex_location": settings.VERTEX_LOCATION,
        "vertex_embed_model": settings.VERTEX_EMBED_MODEL,
    }


@app.post("/v1/similarity/process")
def process_http(body: ProcessRequest):
    payload = body.model_dump()
    if body.brand and not body.brand_name:
        payload["brand_name"] = body.brand
    try:
        return process_upload(payload)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        log.exception("process_http failed")
        raise HTTPException(500, str(exc)) from exc


@app.post("/v1/similarity/compare-pins")
def compare_pins_http(body: ComparePinsRequest):
    """Embed a brand SKU image and score against PIN unique garments (read-only)."""
    if not settings.DATABASE_URL:
        raise HTTPException(503, "DATABASE_URL not set")
    if not body.pin_codes:
        raise HTTPException(400, "pin_codes required")
    if not body.gcs_object_key and not body.embedding:
        raise HTTPException(400, "gcs_object_key or embedding required")
    try:
        from similarity.compare_pins import compare_image_to_pins

        return compare_image_to_pins(
            gcs_object_key=body.gcs_object_key or "",
            gcs_bucket=body.gcs_bucket,
            pin_codes=list(body.pin_codes),
            topk=max(1, min(int(body.topk or 5), 20)),
            embedding=body.embedding,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        log.exception("compare_pins failed")
        raise HTTPException(500, str(exc)) from exc


@app.get("/v1/admin/unique-garments/summary")
def admin_summary(
    pin: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
):
    if not settings.DATABASE_URL:
        raise HTTPException(503, "DATABASE_URL not set")
    try:
        return db.admin_summary(pin=pin or None, brand=brand or None)
    except Exception as exc:
        log.exception("admin_summary failed")
        raise HTTPException(500, f"admin summary failed: {exc}") from exc


@app.get("/v1/admin/unique-garments")
def admin_list(
    pin: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    user_uuid: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    if not settings.DATABASE_URL:
        raise HTTPException(503, "DATABASE_URL not set")
    try:
        return db.list_unique_garments(
            pin=pin or None,
            brand=brand or None,
            user_uuid=user_uuid or None,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        log.exception("admin_list failed")
        raise HTTPException(500, f"admin list failed: {exc}") from exc


@app.post("/")
async def pubsub_push(request: Request):
    """Pub/Sub push endpoint — same ack/retry semantics as suffai-extraction."""
    envelope = await request.json()
    message = envelope.get("message") or {}
    data_b64 = message.get("data")
    message_id = message.get("messageId") or message.get("message_id") or "unknown"
    payload: Dict[str, Any] = {}

    if not data_b64 and not (message.get("attributes") or {}).get("objectId"):
        log.warning("empty pubsub payload message_id=%s", message_id)
        return JSONResponse({"status": "ignored"}, status_code=200)

    try:
        payload = parse_key_object_metadata(envelope)
        payload["idempotency_key"] = payload.get("idempotency_key") or message_id
        payload["message_id"] = message_id
        return process_upload(payload)
    except (ValueError, FileNotFoundError) as exc:
        # Non-retryable → 200 (avoid infinite Pub/Sub retry), same as extraction
        log.error("client_error message_id=%s err=%s", message_id, exc)
        try:
            if settings.DATABASE_URL:
                db.record_event(
                    gcs_bucket=payload.get("gcs_bucket"),
                    gcs_object_key=str(payload.get("gcs_object_key") or "unknown"),
                    user_uuid=payload.get("user_uuid"),
                    pin_code=payload.get("pin_code"),
                    brand_name=payload.get("brand_name") or payload.get("brand"),
                    outcome="error",
                    error_message=str(exc),
                    idempotency_key=str(message_id),
                )
        except Exception:
            log.exception("failed to record error event")
        return JSONResponse(
            {"status": "FAILED", "error": str(exc), "message_id": message_id},
            status_code=200,
        )
    except Exception as exc:
        # Retryable → 500 so Pub/Sub retries / DLQ
        log.error(
            "similarity failed message_id=%s err=%s\n%s",
            message_id,
            exc,
            traceback.format_exc(),
        )
        return JSONResponse(
            {"status": "FAILED", "error": str(exc), "message_id": message_id},
            status_code=500,
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", settings.PORT)))
