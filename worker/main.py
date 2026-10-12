"""
Garment image extraction worker (Cloud Run + Pub/Sub push).

State machine (Blueprint §9):
  PENDING_UPLOAD → UPLOADED → QUEUED → PROCESSING → NEEDS_REVIEW | COMPLETED | FAILED

Vertex AI is opt-in via ENABLE_VERTEX=true. Default: schema-stub abstain path for local/CI.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("suffai-extraction")

app = FastAPI(title="Suffai Extraction Worker", version="1.0.0")


def _abstain_result(reason: str) -> Dict[str, Any]:
    return {
        "commodity_type": "unknown",
        "material_type": "unknown",
        "primary_color": "unknown",
        "size_normalized": None,
        "brand_name": "unknown",
        "brand_id": None,
        "pattern": "unknown",
        "attribute_evidence": {
            "note": {"source": "worker_stub", "confidence": 0.0, "reason": reason}
        },
        "review_status": "needs_review",
        "model_version": os.getenv("VERTEX_MODEL", "stub-v1"),
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "suffai-extraction",
        "vertex_enabled": os.getenv("ENABLE_VERTEX", "false").lower() == "true",
    }


@app.post("/")
async def pubsub_push(request: Request):
    """Pub/Sub push endpoint — idempotent extraction job."""
    envelope = await request.json()
    message = envelope.get("message") or {}
    data_b64 = message.get("data")
    message_id = message.get("messageId") or message.get("message_id") or "unknown"

    if not data_b64:
        log.warning("empty pubsub payload message_id=%s", message_id)
        return JSONResponse({"status": "ignored"}, status_code=200)

    try:
        payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
    except Exception as exc:
        log.exception("bad payload: %s", exc)
        return JSONResponse({"status": "bad_payload"}, status_code=200)

    image_id = payload.get("image_id")
    gcs_object = payload.get("gcs_object_key")
    idempotency_key = payload.get("idempotency_key") or message_id

    log.info(
        "queued extraction image_id=%s object=%s idem=%s",
        image_id,
        gcs_object,
        idempotency_key,
    )

    # Production: download from GCS_IMAGE_BUCKET, call Vertex with strict JSON schema,
    # write extraction_jobs + garment attributes to Cloud SQL, DLQ on hard failure.
    if os.getenv("ENABLE_VERTEX", "false").lower() == "true":
        result = _abstain_result("Vertex path scaffolded — wire client after project approval")
    else:
        result = _abstain_result("ENABLE_VERTEX=false; abstain without paid API call")

    return {
        "status": "NEEDS_REVIEW",
        "idempotency_key": idempotency_key,
        "result": result,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8081")))
