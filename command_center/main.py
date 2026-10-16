"""
Command Center BFF: serves web/ UI + /v1/metrics + /v1/ads-lab/extract.

GCP-native: metrics always from BigQuery (no local CSV fallback).
Does NOT modify api/, worker/, suffai/, or services/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [command_center] %(message)s",
)
log = logging.getLogger("command_center")

REPO = Path(__file__).resolve().parents[1]
WEB_DIR = REPO / "web"
DATA_DIR = REPO / "command_center_data"
SCRIPTS = Path(__file__).resolve().parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import aggregate_metrics as agg  # noqa: E402

app = FastAPI(title="SuffAI Command Center", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PIN_POP = DATA_DIR / "pin_population.json"


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _count_extraction_jobs() -> Optional[int]:
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        log.debug("DATABASE_URL not set; skipping extraction_jobs count")
        return None
    try:
        import psycopg2  # type: ignore

        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM extraction_jobs")
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        log.exception("Cloud SQL extraction_jobs count failed")
        return None


def compute_metrics() -> dict[str, Any]:
    """Load aggregates from BigQuery only. Fail loudly if BQ is unavailable."""
    if not _env_bool("USE_BQ", True):
        log.error("USE_BQ is false — CSV fallback disabled; set USE_BQ=true")
        raise HTTPException(
            503,
            "BigQuery required: set USE_BQ=true (local CSV fallback removed)",
        )

    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
    dataset = os.getenv("BQ_DATASET", "suffai_ops")
    if not project:
        log.error("GOOGLE_CLOUD_PROJECT not set")
        raise HTTPException(503, "GOOGLE_CLOUD_PROJECT is not set")

    if not PIN_POP.is_file():
        log.error("pin_population.json missing path=%s", PIN_POP)
        raise HTTPException(500, f"pin_population.json missing at {PIN_POP}")

    try:
        pin_pop = json.loads(PIN_POP.read_text(encoding="utf-8"))
    except Exception as exc:
        log.exception("Failed to read pin_population.json")
        raise HTTPException(500, f"pin_population.json read failed: {exc}") from exc

    try:
        log.info("Loading metrics from BigQuery project=%s dataset=%s", project, dataset)
        customers, orders, commodities = agg.load_from_bq(project, dataset)
        log.info(
            "BQ rows customers=%s orders=%s commodities=%s",
            len(customers),
            len(orders),
            len(commodities),
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("BigQuery metrics failed project=%s dataset=%s", project, dataset)
        raise HTTPException(
            503,
            f"BigQuery metrics failed (check tables/IAM): {exc}",
        ) from exc

    payload = agg.build(customers, orders, commodities, pin_pop)
    jobs_n = _count_extraction_jobs()
    if jobs_n is not None:
        payload["extraction_jobs_count"] = jobs_n
    payload["source"] = "bigquery"
    return payload


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "suffai-command-center",
        "use_bq": _env_bool("USE_BQ", True),
        "project": bool(os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")),
        "extraction_url": bool(os.getenv("EXTRACTION_URL")),
        "gcs_bucket": bool(os.getenv("GCS_IMAGE_BUCKET")),
        "database_url": bool(os.getenv("DATABASE_URL")),
    }


@app.get("/v1/metrics")
def metrics():
    try:
        return compute_metrics()
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("metrics endpoint unexpected error")
        raise HTTPException(500, f"metrics failed: {exc}") from exc


@app.post("/v1/ads-lab/extract")
async def ads_lab_extract(files: list[UploadFile] = File(...)):
    extraction_url = (os.getenv("EXTRACTION_URL") or "").rstrip("/")
    bucket = os.getenv("GCS_IMAGE_BUCKET") or ""
    if not extraction_url:
        log.error("EXTRACTION_URL not set")
        raise HTTPException(503, "EXTRACTION_URL not set (Cloud Run URL of suffai-extraction)")
    if not bucket:
        log.error("GCS_IMAGE_BUCKET not set")
        raise HTTPException(503, "GCS_IMAGE_BUCKET not set")

    try:
        from google.cloud import storage  # type: ignore
        import google.auth.transport.requests  # type: ignore
        import google.oauth2.id_token  # type: ignore
        import urllib.request
    except ImportError as exc:
        log.exception("Missing GCP client libraries")
        raise HTTPException(503, f"Missing GCP libs: {exc}") from exc

    try:
        client = storage.Client()
        bkt = client.bucket(bucket)
    except Exception as exc:
        log.exception("GCS client init failed bucket=%s", bucket)
        raise HTTPException(503, f"GCS unavailable: {exc}") from exc

    results: list[dict[str, Any]] = []

    auth_req = google.auth.transport.requests.Request()
    try:
        token = google.oauth2.id_token.fetch_id_token(auth_req, extraction_url)
    except Exception:
        log.warning("Could not fetch ID token for EXTRACTION_URL; calling without auth")
        token = None

    for f in files:
        if not f.content_type or not f.content_type.startswith("image/"):
            log.warning("Skipping non-image upload filename=%s type=%s", f.filename, f.content_type)
            continue
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(
            f.content_type, "jpg"
        )
        object_key = f"demo/{uuid.uuid4().hex}.{ext}"
        data = await f.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(400, f"{f.filename} too large (max 10MB)")

        try:
            blob = bkt.blob(object_key)
            blob.upload_from_string(data, content_type=f.content_type)
            log.info("Uploaded to GCS gs://%s/%s filename=%s", bucket, object_key, f.filename)
        except Exception as exc:
            log.exception("GCS upload failed key=%s", object_key)
            results.append(
                {
                    "filename": f.filename,
                    "object_key": object_key,
                    "ok": False,
                    "error": f"GCS upload failed: {exc}",
                }
            )
            continue

        idem = f"ads-lab-{uuid.uuid4().hex}"
        body = json.dumps(
            {
                "gcs_object_key": object_key,
                "gcs_bucket": bucket,
                "target_type": "garment",
                "idempotency_key": idem,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            f"{extraction_url}/v1/extract",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            log.info(
                "Extract ok filename=%s status=%s image_id=%s",
                f.filename,
                payload.get("status"),
                payload.get("image_id"),
            )
        except Exception as exc:
            log.exception("Extract call failed filename=%s key=%s", f.filename, object_key)
            results.append(
                {
                    "filename": f.filename,
                    "object_key": object_key,
                    "ok": False,
                    "error": str(exc),
                }
            )
            continue

        attrs = payload.get("result") or {}
        results.append(
            {
                "filename": f.filename,
                "object_key": object_key,
                "ok": True,
                "status": payload.get("status"),
                "image_id": payload.get("image_id"),
                "attributes": {
                    "color": attrs.get("color") or attrs.get("dominant_color"),
                    "brand": attrs.get("brand") or attrs.get("brand_name"),
                    "size": attrs.get("size"),
                    "material": attrs.get("material"),
                    "pattern": attrs.get("pattern"),
                    "sleeve_type": attrs.get("sleeve_type") or attrs.get("sleeve"),
                    "collar_type": attrs.get("collar_type") or attrs.get("collar"),
                    "name": attrs.get("garment_type") or attrs.get("name") or attrs.get("category"),
                    "confidence": attrs.get("confidence") or attrs.get("confidence_score"),
                },
                "raw_result": attrs,
            }
        )

    if not results:
        raise HTTPException(400, "No valid images uploaded")

    return {"ok": True, "items": results}


@app.post("/v1/chat/assistant")
async def chat_assistant_proxy(request: Request):
    """Forward browser chat requests to the existing suffai-api service."""
    api_base = (os.getenv("API_PUBLIC_URL") or "").rstrip("/")
    if not api_base:
        raise HTTPException(503, "API_PUBLIC_URL is not configured on Command Center")

    body = await request.body()
    if len(body) > 40000:
        raise HTTPException(413, "Chat request is too large")

    def forward():
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{api_base}/v1/chat/assistant",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    status, content = await asyncio.to_thread(forward)
    return Response(content=content, status_code=status, media_type="application/json")


@app.get("/")
def root():
    if (WEB_DIR / "login.html").exists():
        return RedirectResponse(url="/login.html")
    return {
        "service": "suffai-command-center",
        "endpoints": ["/health", "/v1/metrics", "POST /v1/ads-lab/extract"],
    }


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")
