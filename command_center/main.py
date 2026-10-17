"""
Command Center BFF: serves web/ UI + metrics + ads-lab extract/campaign + chat + admin proxy.

GCP-native: metrics always from BigQuery (no local CSV fallback).
Chat answers from web/metrics.json summary + UI filters via Vertex Gemini.
Admin unique-garments proxies to suffai-similarity.
Does NOT modify worker/ (extraction) or similarity/ core ingest.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from urllib.parse import urlencode

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [command_center] %(message)s",
)
log = logging.getLogger("command_center")

REPO = Path(__file__).resolve().parents[1]
WEB_DIR = REPO / "web"
DATA_DIR = REPO / "command_center_data"
CC_DIR = Path(__file__).resolve().parent
SCRIPTS = CC_DIR / "scripts"
sys.path.insert(0, str(CC_DIR))
sys.path.insert(0, str(SCRIPTS))

import aggregate_metrics as agg  # noqa: E402
import auth_acl  # noqa: E402
import brand_campaign as brand_camp  # noqa: E402
import aura_agent  # noqa: E402
import aura_guard  # noqa: E402
import campaign_jobs  # noqa: E402
import campaign_service as camp_svc  # noqa: E402
import chatbot  # noqa: E402
import style_assistant as style_asst  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

app = FastAPI(title="SuffAI Command Center", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PIN_POP = DATA_DIR / "pin_population.json"


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/v1/agent/"):
        rid = uuid.uuid4().hex[:12]
        log.warning("request_id=%s agent request validation failed: %s", rid, exc.errors()[:3])
        return JSONResponse(status_code=400, content=aura_guard.error_body("invalid_input", rid))
    return await request_validation_exception_handler(request, exc)


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


METRICS_JSON = WEB_DIR / "metrics.json"
camp_svc.init(metrics_loader=compute_metrics, metrics_json=METRICS_JSON)


def _svc_http(exc: camp_svc.ServiceError) -> HTTPException:
    return HTTPException(exc.status, exc.public)


class GeoBriefRequest(BaseModel):
    """Aggregated, PII-free geo store payload for Gemini brief + store decision."""

    filters: dict[str, Any] = Field(default_factory=dict)
    kpis: dict[str, Any] = Field(default_factory=dict)
    focus_pin: Optional[dict[str, Any]] = None
    top_pins: list[dict[str, Any]] = Field(default_factory=list)
    trends: dict[str, Any] = Field(default_factory=dict)
    analysis_brand: Optional[str] = None
    role: str = Field(default="admin")


class StyleRecommendRequest(BaseModel):
    user_uuid: str = Field(default="", min_length=0)
    brand_name: str = Field(..., min_length=1)
    limit: int = Field(default=6, ge=1, le=12)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


def _token_from_request(
    request: Request,
    authorization: Optional[str] = None,
    x_session_token: Optional[str] = None,
) -> Optional[str]:
    if x_session_token and x_session_token.strip():
        return x_session_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    # Also accept query ?token= for simple static HTML fetches
    q = request.query_params.get("token")
    return q.strip() if q else None


def _require_session(
    request: Request,
    authorization: Optional[str] = None,
    x_session_token: Optional[str] = None,
) -> dict[str, Any]:
    token = _token_from_request(request, authorization, x_session_token)
    sess = auth_acl.get_session(token)
    if not sess:
        raise HTTPException(401, "Not authenticated — sign in again")
    return sess


_PII_KEY_DENY = aura_guard.PII_KEY_DENY
_strip_pii_keys = aura_guard.strip_pii_keys


def _sanitize_geo_brief_payload(body: GeoBriefRequest) -> dict[str, Any]:
    """Allowlist aggregates only — no customer rows, no lat/lon, no personal names."""

    def _commodities(raw: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(raw, list):
            return out
        for item in raw[:12]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("value") or item.get("commodity") or "").strip()
            if not name or len(name) > 80:
                continue
            try:
                count = float(item.get("count") or item.get("garments") or 0)
            except (TypeError, ValueError):
                continue
            # Use "commodity" not "name" — "name" is stripped as PII by _strip_pii_keys
            out.append({"commodity": name[:64], "count": count})
        return out

    def _pin_row(raw: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        pin = str(raw.get("pin") or raw.get("pin_code") or "").strip()
        if not pin:
            return None
        heuristic = str(raw.get("heuristic_decision") or raw.get("decision") or "").strip()
        if heuristic not in {"Yes", "Consider", "No"}:
            heuristic = "Consider"
        row: dict[str, Any] = {
            "pin": pin[:12],
            "area_label": str(raw.get("area_label") or pin)[:80],
            "heuristic_decision": heuristic,
            "decision_prior": heuristic,
        }
        for num_key in (
            "score",
            "est_footfall",
            "demand",
            "catchment_pop",
            "garment_pull",
            "revenue",
            "brand_garments",
            "competitor_garments",
            "brand_share",
        ):
            if num_key not in raw:
                continue
            try:
                row[num_key] = float(raw[num_key])
            except (TypeError, ValueError):
                continue
        commodities = _commodities(raw.get("commodities"))
        if commodities:
            row["commodities"] = commodities
        return row

    filters_in = body.filters if isinstance(body.filters, dict) else {}
    filters = {
        "pins": [str(p)[:12] for p in (filters_in.get("pins") or filters_in.get("pin") or []) if p][:20],
        "age": [str(a)[:32] for a in (filters_in.get("age") or []) if a][:20],
        "gender": [str(g)[:32] for g in (filters_in.get("gender") or []) if g][:10],
        "rfm": [str(r)[:64] for r in (filters_in.get("rfm") or []) if r][:20],
        "brand": [str(b)[:64] for b in (filters_in.get("brand") or []) if b][:20],
    }

    kpis_in = body.kpis if isinstance(body.kpis, dict) else {}
    kpis: dict[str, Any] = {}
    for k in ("customers", "orders", "revenue", "garments"):
        if k not in kpis_in:
            continue
        try:
            kpis[k] = float(kpis_in[k])
        except (TypeError, ValueError):
            continue

    trends_in = body.trends if isinstance(body.trends, dict) else {}
    trends = {
        k: str(trends_in[k])[:64]
        for k in ("top_commodity", "top_color", "top_brand", "top_pattern", "top_size")
        if trends_in.get(k)
    }

    top_pins = []
    for item in (body.top_pins or [])[:8]:
        row = _pin_row(item)
        if row:
            top_pins.append(row)

    focus = _pin_row(body.focus_pin) if body.focus_pin else None
    role = str(body.role or "admin").strip().lower()
    if role not in {"brand", "customer", "admin"}:
        role = "admin"
    analysis_brand = str(body.analysis_brand or "").strip()[:64] or None
    if analysis_brand and analysis_brand.lower() in {"competitors", "all", "unknown"}:
        analysis_brand = None

    payload = {
        "filters": filters,
        "kpis": kpis,
        "analysis_brand": analysis_brand,
        "focus_pin": focus,
        "top_pins": top_pins,
        "trends": trends,
        "role": role,
        "notes": {
            "centroid": "fixed_at_pincode_hub",
            "footfall": "estimated_from_filtered_demand",
            "competition": "brand_garments_vs_competitor_garments_in_PIN_under_filters",
            "pii": "none_in_payload",
        },
    }
    return _strip_pii_keys(payload)


def _parse_geo_brief_model_json(text: str) -> dict[str, Any]:
    """Extract decision JSON from model output; tolerate optional fences."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty model response")
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start_i = raw.find("{")
    end_i = raw.rfind("}")
    if start_i < 0 or end_i <= start_i:
        raise ValueError("no JSON object in model response")
    data = json.loads(raw[start_i : end_i + 1])
    if not isinstance(data, dict):
        raise ValueError("model JSON is not an object")
    decision = str(data.get("decision") or "").strip()
    if decision not in {"Yes", "Consider", "No"}:
        raise ValueError(f"invalid decision: {decision!r}")
    explanation = str(data.get("explanation") or "").strip()
    brief = str(data.get("brief") or "").strip()
    if not explanation:
        raise ValueError("missing explanation")
    if not brief:
        brief = explanation
    confidence = str(data.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    factors = data.get("key_factors") or []
    if not isinstance(factors, list):
        factors = []
    key_factors = [str(x).strip()[:120] for x in factors if str(x).strip()][:8]
    return {
        "decision": decision,
        "confidence": confidence,
        "explanation": explanation[:4000],
        "brief": brief[:4000],
        "key_factors": key_factors,
    }


def _heuristic_store_decision_from_focus(focus: Optional[dict[str, Any]]) -> str:
    """Rule fallback when Vertex is unavailable — brand share + demand."""
    if not focus:
        return "No"
    demand = float(focus.get("demand") or 0)
    brand_g = float(focus.get("brand_garments") or 0)
    share = focus.get("brand_share")
    foot = float(focus.get("est_footfall") or 0)
    if demand <= 0 or brand_g <= 0:
        return "No"
    if share is None:
        return "Consider" if foot >= 40 else "No"
    share_f = float(share)
    if share_f >= 0.35 and foot >= 60:
        return "Yes"
    if share_f >= 0.22 or (brand_g >= 3 and foot >= 40):
        return "Consider"
    return "No"


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
        "enable_vertex": _env_bool("ENABLE_VERTEX", False),
        "vertex_chat_model": os.getenv("VERTEX_CHAT_MODEL", "gemini-2.0-flash"),
        "metrics_json": METRICS_JSON.is_file(),
        "chat": True,
        "similarity_url": bool(os.getenv("SIMILARITY_URL")),
        "ads_lab_campaign": True,
        "admin_proxy": True,
        "style_assistant": True,
        "auth": True,
        "geo_brief": True,
    }


@app.post("/v1/auth/login")
def auth_login(body: LoginRequest):
    try:
        sess = auth_acl.authenticate(body.username, body.password)
    except PermissionError:
        raise HTTPException(401, "Invalid username or password") from None
    except Exception as exc:
        log.exception("auth login failed")
        raise HTTPException(503, f"Auth unavailable: {exc}") from exc
    log.info("auth login ok user=%s role=%s", sess.get("username"), sess.get("role"))
    return {"ok": True, **sess}


@app.get("/v1/auth/me")
def auth_me(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    sess = _require_session(request, authorization, x_session_token)
    return {"ok": True, **sess}


@app.post("/v1/auth/logout")
def auth_logout(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    token = _token_from_request(request, authorization, x_session_token)
    auth_acl.destroy_session(token)
    return {"ok": True}


@app.get("/v1/metrics")
def metrics(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
):
    """Geo metrics from BQ. Auth headers optional (brand masking when session present)."""
    try:
        payload = compute_metrics()
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("metrics endpoint unexpected error")
        raise HTTPException(500, f"metrics failed: {exc}") from exc

    # Optional session: brand role gets Competitors masking
    try:
        token = _token_from_request(request, authorization, x_session_token)
        sess = auth_acl.get_session(token) if token else None
        if sess and sess.get("role") == "brand" and sess.get("brand_name"):
            payload = auth_acl.mask_metrics_for_brand(payload, str(sess["brand_name"]))
    except Exception as exc:
        # Never fail metrics on session parse — log and return unmasked payload
        log.warning("metrics session mask skipped: %s", exc)
    return payload


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
                "attributes": camp_svc.extracted_attributes(
                    attrs, camp_svc.known_value(attrs.get("brand_name"), attrs.get("brand"))
                ),
                "raw_result": attrs,
            }
        )

    if not results:
        raise HTTPException(400, "No valid images uploaded")

    return {"ok": True, "items": results}


def _parse_pin_codes(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(p).strip() for p in data if str(p).strip()]
    except Exception:
        pass
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


@app.post("/v1/ads-lab/campaign")
async def ads_lab_campaign(
    request: Request,
    files: list[UploadFile] = File(...),
    brand_name: str = Form(...),
    pin_codes: str = Form(...),
    age_group: str = Form(""),
    gender: str = Form(""),
    rfm_segment: str = Form(""),
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """
    Brand SKU campaign: GCS brands/{brand}/… → extract (brand overwritten) →
    compare-pins → rank with PIN + age/gender/RFM metrics profile.
    Does not modify suffai-extraction.
    """
    sess = _require_session(request, authorization, x_session_token)
    if not files:
        raise HTTPException(400, "files required")
    images: list[camp_svc.ImageInput] = []
    for f in files:
        if not f.content_type or not f.content_type.startswith("image/"):
            continue
        images.append(
            camp_svc.ImageInput(
                filename=f.filename or "sku",
                content_type=f.content_type,
                data=await f.read(),
            )
        )
    try:
        return await run_in_threadpool(
            camp_svc.create_campaign_from_images,
            sess,
            images,
            brand_name=brand_name,
            pin_codes=_parse_pin_codes(pin_codes),
            age_group=age_group,
            gender=gender,
            rfm_segment=rfm_segment,
        )
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc


async def _read_images(files: list[UploadFile]) -> list[camp_svc.ImageInput]:
    out: list[camp_svc.ImageInput] = []
    for f in files or []:
        if not f.content_type or not f.content_type.startswith("image/"):
            continue
        out.append(
            camp_svc.ImageInput(filename=f.filename or "sku", content_type=f.content_type, data=await f.read())
        )
    return out


@app.post("/v1/ads-lab/campaign-jobs")
async def ads_lab_submit_campaign_job(
    request: Request,
    files: list[UploadFile] = File(...),
    brand_name: str = Form(""),
    pin_codes: str = Form(...),
    age_group: str = Form(""),
    gender: str = Form(""),
    rfm_segment: str = Form(""),
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """Async extract & rank: uploads images, returns the job with counts + ETA immediately."""
    sess = _require_session(request, authorization, x_session_token)
    images = await _read_images(files)
    try:
        job = await run_in_threadpool(
            campaign_jobs.submit_job,
            sess,
            images,
            brand_name=brand_name,
            pin_codes=_parse_pin_codes(pin_codes),
            age_group=age_group,
            gender=gender,
            rfm_segment=rfm_segment,
        )
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, "job": job}


@app.get("/v1/ads-lab/campaign-jobs")
def ads_lab_list_campaign_jobs(
    request: Request,
    active: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    sess = _require_session(request, authorization, x_session_token)
    try:
        jobs = campaign_jobs.list_jobs(sess, active=active, limit=limit)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, "jobs": jobs, "count": len(jobs)}


@app.get("/v1/ads-lab/campaign-jobs/{job_id}")
def ads_lab_get_campaign_job(
    job_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    sess = _require_session(request, authorization, x_session_token)
    try:
        job = campaign_jobs.get_job(sess, job_id)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, "job": job}


@app.post("/internal/campaign-jobs/{job_id}/run", include_in_schema=False)
def internal_run_campaign_job(
    job_id: str,
    authorization: Optional[str] = Header(None),
):
    """Cloud Tasks worker. Accepts only an OIDC token minted for TASKS_SERVICE_ACCOUNT."""
    if not campaign_jobs.verify_task_request(authorization):
        raise HTTPException(403, "Forbidden")
    try:
        jid = str(uuid.UUID(job_id))
    except ValueError:
        return {"ok": True, "skipped": True}
    try:
        return campaign_jobs.run_chunk(jid)
    except Exception as exc:
        log.exception("campaign job chunk failed job=%s", jid)
        raise HTTPException(500, "chunk failed; will retry") from exc


def _campaign_access(
    campaign: dict[str, Any],
    sess: Optional[dict[str, Any]],
) -> None:
    try:
        camp_svc.check_access(campaign, sess)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc


@app.get("/v1/ads-lab/campaigns")
def ads_lab_list_campaigns(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
):
    sess = _require_session(request, authorization, x_session_token)
    try:
        rows = camp_svc.list_for(sess, status=status, limit=limit)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, "campaigns": rows, "count": len(rows)}


@app.get("/v1/ads-lab/campaigns/{campaign_id}")
def ads_lab_campaign_detail(
    campaign_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    sess = _require_session(request, authorization, x_session_token)
    try:
        data = camp_svc.get_with_skus(sess, campaign_id)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, **data}


@app.post("/v1/ads-lab/campaigns/{campaign_id}/start")
def ads_lab_campaign_start(
    campaign_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """Publish ranked campaign to Style Assistant for filter-matched customers (1P)."""
    campaign, _ = _load_campaign_for(campaign_id, request, authorization, x_session_token)
    campaign_id = campaign["campaign_id"]
    if campaign.get("status") not in {"ranked", "started"}:
        raise HTTPException(
            400,
            f"Campaign must be ranked before start (status={campaign.get('status')})",
        )
    try:
        brand_camp.set_campaign_status(campaign_id, "started")
        result = style_asst.materialize_campaign_recommendations(campaign_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        log.exception("campaign start failed id=%s", campaign_id)
        try:
            brand_camp.set_campaign_status(campaign_id, "error", str(exc)[:500])
        except Exception:
            pass
        raise HTTPException(503, f"campaign start failed: {exc}") from exc
    refreshed = brand_camp.get_campaign(campaign_id) or campaign
    return {"ok": True, "campaign": refreshed, "materialize": result}


@app.post("/v1/ads-lab/campaigns/{campaign_id}/ignore")
def ads_lab_campaign_ignore(
    campaign_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    sess = _require_session(request, authorization, x_session_token)
    try:
        campaign = camp_svc.discard(sess, campaign_id)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, "campaign": campaign}


class CampaignConfigRequest(BaseModel):
    campaign_name: str = Field(..., min_length=1, max_length=120)
    objective: str
    budget_inr: float = Field(..., gt=0)
    duration_days: int = Field(..., ge=1, le=365)
    start_date: Optional[str] = None
    selected_sku_ids: list[str] = Field(default_factory=list)
    creative: dict[str, Any] = Field(default_factory=dict)
    source_context: Optional[dict[str, Any]] = None
    channels: Optional[list[str]] = None
    message_template: Optional[dict[str, str]] = None


class CampaignEstimateRequest(BaseModel):
    objective: Optional[str] = None
    budget_inr: float = Field(0, ge=0)
    duration_days: int = Field(14, ge=1, le=365)
    channels: Optional[list[str]] = None


class BudgetRecommendationRequest(BaseModel):
    objective: Optional[str] = None
    duration_days: int = Field(14, ge=1, le=365)
    channels: Optional[list[str]] = None


def _load_campaign_for(
    campaign_id: str,
    request: Request,
    authorization: Optional[str],
    x_session_token: Optional[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    sess = _require_session(request, authorization, x_session_token)
    try:
        campaign = camp_svc.load_campaign(sess, campaign_id)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return campaign, sess


@app.patch("/v1/ads-lab/campaigns/{campaign_id}/config")
def ads_lab_campaign_config(
    campaign_id: str,
    body: CampaignConfigRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """Save campaign name, objective, SKUs, creative, budget and schedule (status → configured)."""
    sess = _require_session(request, authorization, x_session_token)
    try:
        campaign = camp_svc.configure(
            sess,
            campaign_id,
            campaign_name=body.campaign_name,
            objective=body.objective,
            budget_inr=body.budget_inr,
            duration_days=body.duration_days,
            start_date=body.start_date,
            selected_sku_ids=body.selected_sku_ids,
            creative=body.creative,
            source_context=body.source_context,
            channels=body.channels,
            message_template=body.message_template,
        )
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, "campaign": campaign}


@app.post("/v1/ads-lab/campaigns/{campaign_id}/budget-recommendation")
def ads_lab_budget_recommendation(
    campaign_id: str,
    body: BudgetRecommendationRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """Simulated budget recommendation from audience, objective, duration and channels."""
    sess = _require_session(request, authorization, x_session_token)
    try:
        rec = camp_svc.budget_recommendation(
            sess, campaign_id, objective=body.objective,
            duration_days=body.duration_days, channels=body.channels,
        )
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, "recommendation": rec}


@app.post("/v1/ads-lab/campaigns/{campaign_id}/export")
def ads_lab_campaign_export(
    campaign_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """3P: mark exported and return the ranked SKUs + audience for running on another platform."""
    sess = _require_session(request, authorization, x_session_token)
    try:
        data = camp_svc.export_3p(sess, campaign_id)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, **data}


@app.post("/v1/ads-lab/campaigns/{campaign_id}/estimate")
def ads_lab_campaign_estimate(
    campaign_id: str,
    body: CampaignEstimateRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """Simulated outcome ranges from budget, duration and audience size (nothing persisted)."""
    sess = _require_session(request, authorization, x_session_token)
    try:
        est = camp_svc.estimate(
            sess,
            campaign_id,
            objective=body.objective,
            budget_inr=body.budget_inr,
            duration_days=body.duration_days,
            channels=body.channels,
        )
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    return {"ok": True, "estimate": est}


@app.post("/v1/ads-lab/campaigns/{campaign_id}/launch")
def ads_lab_campaign_launch(
    campaign_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """
    Simulated launch: persist status + final synthetic KPIs + daily series.
    No Style Assistant recommendations are created and nothing is delivered to customers.
    """
    sess = _require_session(request, authorization, x_session_token)
    try:
        result = camp_svc.launch(sess, campaign_id)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc
    if result["already_launched"]:
        return {"ok": True, "campaign": result["campaign"], "already_launched": True}
    return {"ok": True, "campaign": result["campaign"], "impact": result["impact"]}


@app.get("/v1/ads-lab/campaigns/{campaign_id}/monitor")
def ads_lab_campaign_monitor(
    campaign_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """Campaign config + selected SKUs + simulated KPIs + daily series + insights."""
    sess = _require_session(request, authorization, x_session_token)
    try:
        return camp_svc.monitor_payload(sess, campaign_id)
    except camp_svc.ServiceError as exc:
        raise _svc_http(exc) from exc


@app.get("/v1/admin/campaigns")
def admin_list_campaigns(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    limit: int = Query(100, ge=1, le=500),
):
    sess = _require_session(request, authorization, x_session_token)
    if sess.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    try:
        rows = brand_camp.list_campaigns(brand_name=None, limit=limit)
    except Exception as exc:
        raise HTTPException(503, f"admin campaigns failed: {exc}") from exc
    return {"ok": True, "campaigns": rows, "count": len(rows)}


@app.get("/v1/style-assistant/feed")
def style_assistant_feed(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    limit: int = Query(12, ge=1, le=40),
):
    """Customer Style feed from started 1P campaigns (no campaign labels in payload)."""
    sess = _require_session(request, authorization, x_session_token)
    role = sess.get("role")
    if role == "brand":
        raise HTTPException(403, "Brand accounts cannot use Style feed")
    user_uuid = ""
    if role == "customer":
        user_uuid = (sess.get("customer_id") or "").strip()
    elif role == "admin":
        # Admin can preview via ?user_uuid=
        user_uuid = (request.query_params.get("user_uuid") or "").strip()
    if not user_uuid:
        raise HTTPException(400, "customer_id / user_uuid required")
    try:
        payload = style_asst.customer_style_feed(user_uuid, limit=limit)
    except Exception as exc:
        log.exception("style feed failed user=%s", user_uuid)
        raise HTTPException(503, f"style feed failed: {exc}") from exc
    return payload


def _similarity_id_token(audience: str) -> Optional[str]:
    try:
        import google.auth.transport.requests  # type: ignore
        import google.oauth2.id_token  # type: ignore

        auth_req = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(auth_req, audience)
    except Exception:
        log.warning("Could not fetch ID token for similarity audience=%s", audience)
        return None


def _proxy_similarity_get(path: str, params: dict[str, Any]) -> Any:
    """GET proxy to suffai-similarity (Admin unique-garments)."""
    import urllib.error
    import urllib.request

    base = (os.getenv("SIMILARITY_URL") or "").rstrip("/")
    if not base:
        raise HTTPException(
            503,
            "SIMILARITY_URL not set (Cloud Run URL of suffai-similarity)",
        )

    qs = urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    url = f"{base}{path}" + (f"?{qs}" if qs else "")
    token = _similarity_id_token(base)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        detail = body
        try:
            parsed = json.loads(body)
            detail = parsed.get("detail") or body
        except Exception:
            pass
        log.error("similarity proxy HTTP %s path=%s detail=%s", exc.code, path, detail)
        raise HTTPException(exc.code, f"similarity: {detail}") from exc
    except Exception as exc:
        log.exception("similarity proxy failed path=%s", path)
        raise HTTPException(503, f"similarity unreachable: {exc}") from exc


@app.get("/v1/style-assistant/users")
def style_assistant_users(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    limit: int = Query(200, ge=1, le=500),
):
    """Distinct customer ids from user_wardrobe for Style Assistant dropdown."""
    token = _token_from_request(request, authorization, x_session_token)
    sess = auth_acl.get_session(token) if token else None
    if sess and sess.get("role") == "brand":
        raise HTTPException(403, "Brand accounts only get the campaign preview in Style Assistant")
    if not os.getenv("DATABASE_URL", "").strip():
        raise HTTPException(503, "DATABASE_URL not set")
    try:
        users = style_asst.list_style_users(limit=limit)
        return {"users": users, "count": len(users)}
    except Exception as exc:
        log.exception("style-assistant users failed")
        raise HTTPException(503, f"style-assistant users failed: {exc}") from exc


@app.get("/v1/style-assistant/brands")
def style_assistant_brands(limit: int = Query(200, ge=1, le=500)):
    """Distinct advertising brands from brand_skus for Style Assistant dropdown."""
    if not os.getenv("DATABASE_URL", "").strip():
        raise HTTPException(503, "DATABASE_URL not set")
    try:
        brands = style_asst.list_style_brands(limit=limit)
        return {"brands": brands, "count": len(brands)}
    except Exception as exc:
        log.exception("style-assistant brands failed")
        raise HTTPException(503, f"style-assistant brands failed: {exc}") from exc


@app.post("/v1/style-assistant/recommend")
def style_assistant_recommend(
    body: StyleRecommendRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """
    Feature 2: rank brand SKUs against a user's wardrobe (Feature 1 blend),
    then Vertex LLM builds wardrobe + SKU outfit combinations with copy.
    """
    request_id = uuid.uuid4().hex[:12]
    user_uuid = (body.user_uuid or "").strip()
    brand_name = (body.brand_name or "").strip()

    token = _token_from_request(request, authorization, x_session_token)
    sess = auth_acl.get_session(token) if token else None
    if sess and sess.get("role") == "customer":
        forced = (sess.get("customer_id") or "").strip()
        if not forced:
            raise HTTPException(403, "Customer session missing customer_id")
        user_uuid = forced
    elif sess and sess.get("role") == "brand":
        raise HTTPException(403, "Brand accounts cannot use Style Assistant")

    if not user_uuid:
        raise HTTPException(400, "user_uuid is required")
    if not brand_name:
        raise HTTPException(400, "brand_name is required")
    if not os.getenv("DATABASE_URL", "").strip():
        raise HTTPException(503, "DATABASE_URL not set")
    if not _env_bool("ENABLE_VERTEX", False):
        raise HTTPException(
            503,
            "Style Assistant needs ENABLE_VERTEX=true (outfit copy via Vertex)",
        )

    log.info(
        "request_id=%s style recommend user=%s brand=%s limit=%s",
        request_id,
        user_uuid,
        brand_name,
        body.limit,
    )
    try:
        payload = style_asst.recommend_looks(
            user_uuid=user_uuid,
            brand_name=brand_name,
            limit=body.limit,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        log.exception("request_id=%s style recommend failed", request_id)
        raise HTTPException(503, f"style recommend failed: {exc}") from exc

    payload["request_id"] = request_id
    return payload


@app.get("/v1/admin/unique-summary")
def admin_unique_summary(
    pin: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
):
    """Proxy → suffai-similarity GET /v1/admin/unique-garments/summary."""
    return _proxy_similarity_get(
        "/v1/admin/unique-garments/summary",
        {"pin": pin, "brand": brand},
    )


@app.get("/v1/admin/unique-garments")
def admin_unique_garments(
    pin: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    user_uuid: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Proxy → suffai-similarity GET /v1/admin/unique-garments."""
    return _proxy_similarity_get(
        "/v1/admin/unique-garments",
        {
            "pin": pin,
            "brand": brand,
            "user_uuid": user_uuid,
            "limit": limit,
            "offset": offset,
        },
    )


@app.post("/v1/geo/brief")
def geo_brief(
    body: GeoBriefRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """
    Gemini store decision + Market brief for Geo.
    Payload must be aggregates only (brand vs competitor share, commodities, footfall).
    """
    token = _token_from_request(request, authorization, x_session_token)
    sess = auth_acl.get_session(token) if token else None
    return geo_brief_core(body, sess)


def geo_brief_core(body: GeoBriefRequest, sess: Optional[dict[str, Any]]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:12]
    role = (sess or {}).get("role") or body.role or "admin"

    try:
        body.role = str(role)
        if sess and sess.get("role") == "brand" and sess.get("brand_name"):
            # Brand sessions always analyze their own brand (ignore spoofed analysis_brand)
            body.analysis_brand = str(sess.get("brand_name"))
        payload = _sanitize_geo_brief_payload(body)
    except Exception as exc:
        log.exception("request_id=%s geo_brief sanitize failed", request_id)
        raise HTTPException(400, f"Invalid geo brief payload: {exc}") from exc

    focus = payload.get("focus_pin") or {}
    heuristic = _heuristic_store_decision_from_focus(focus)
    log.info(
        "request_id=%s geo_brief start role=%s brand=%s focus_pin=%s "
        "brand_share=%s brand_g=%s comp_g=%s heuristic=%s",
        request_id,
        role,
        payload.get("analysis_brand"),
        focus.get("pin"),
        focus.get("brand_share"),
        focus.get("brand_garments"),
        focus.get("competitor_garments"),
        heuristic,
    )

    def _fallback_payload(detail: str) -> dict[str, Any]:
        """Always return usable decision+brief (HTTP 200) when Vertex fails."""
        explanation = (
            f"Heuristic decision ({heuristic}) for PIN {focus.get('pin') or 'n/a'}: "
            f"brand_share={focus.get('brand_share')}, "
            f"brand_garments={focus.get('brand_garments')}, "
            f"competitor_garments={focus.get('competitor_garments')}, "
            f"est_footfall={focus.get('est_footfall')}, demand={focus.get('demand')}. "
            f"Gemini unavailable ({detail})."
        )
        commodities = focus.get("commodities") or []
        top_c = ", ".join(
            f"{c.get('commodity') or c.get('name')} ({c.get('count')})"
            for c in commodities[:3]
            if isinstance(c, dict)
        )
        brief = (
            f"Open store? {heuristic} at {focus.get('area_label') or focus.get('pin') or 'hub'} "
            f"(PIN {focus.get('pin') or '—'}). Estimated footfall ~{focus.get('est_footfall') or 0}. "
            f"Brand vs competitors: share {focus.get('brand_share') if focus.get('brand_share') is not None else 'n/a'}. "
            f"Commodities: {top_c or 'none in filter slice'}."
        )
        log.warning(
            "request_id=%s geo_brief fallback decision=%s detail=%s",
            request_id,
            heuristic,
            detail,
        )
        return {
            "ok": True,
            "fallback": True,
            "detail": detail,
            "request_id": request_id,
            "decision": heuristic,
            "confidence": "low",
            "explanation": explanation,
            "key_factors": [
                f"heuristic={heuristic}",
                f"brand_share={focus.get('brand_share')}",
                f"est_footfall={focus.get('est_footfall')}",
            ],
            "brief": brief,
            "model": "heuristic",
        }

    if not _env_bool("ENABLE_VERTEX", False):
        return _fallback_payload("ENABLE_VERTEX=false")

    try:
        raw = chatbot.call_vertex_chat(
            "Return the store-opening decision JSON now.",
            payload,
            request_id,
            system=chatbot.GEO_BRIEF_SYSTEM,
            max_context_chars=chatbot.GEO_BRIEF_MAX_CHARS,
        )
        parsed = _parse_geo_brief_model_json(raw)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        log.error("request_id=%s geo_brief vertex HTTP status=%s detail=%s", request_id, exc.status_code, detail)
        return _fallback_payload(detail)
    except Exception as exc:
        log.exception("request_id=%s geo_brief model parse/call failed", request_id)
        return _fallback_payload(str(exc))

    log.info(
        "request_id=%s geo_brief ok decision=%s confidence=%s brief_len=%s",
        request_id,
        parsed["decision"],
        parsed["confidence"],
        len(parsed["brief"]),
    )
    return {
        "ok": True,
        "fallback": False,
        "request_id": request_id,
        "model": os.getenv("VERTEX_CHAT_MODEL", "gemini-2.0-flash"),
        "decision": parsed["decision"],
        "confidence": parsed["confidence"],
        "explanation": parsed["explanation"],
        "key_factors": parsed["key_factors"],
        "brief": parsed["brief"],
    }


def _aura_geo_brief(payload: dict[str, Any], sess: dict[str, Any]) -> dict[str, Any]:
    return geo_brief_core(GeoBriefRequest(**payload), sess)


aura_agent.configure(geo_brief=_aura_geo_brief)


@app.post("/v1/chat")
def chat(body: chatbot.ChatRequest):
    """Dashboard Q&A from metrics.json summary + Geo/Ads UI filters (read-only)."""
    return chatbot.run_chat(body, METRICS_JSON)


@app.post("/v1/agent/chat")
async def agent_chat(
    body: aura_agent.AgentChatRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """AURA brand operating agent — guarded tool calls for Geo + Ads Lab (session required)."""
    token = _token_from_request(request, authorization, x_session_token)
    sess = auth_acl.get_session(token) if token else None
    return await run_in_threadpool(aura_agent.run_agent, body, sess=sess, metrics_path=METRICS_JSON)


@app.post("/v1/agent/uploads")
async def agent_uploads(
    request: Request,
    files: list[UploadFile] = File(...),
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """Stage SKU images for AURA (validated, metadata stripped, session-scoped, short TTL)."""
    token = _token_from_request(request, authorization, x_session_token)
    sess = auth_acl.get_session(token) if token else None
    max_bytes = aura_guard.AURA_MAX_UPLOAD_MB * 1024 * 1024
    raw: list[tuple[str, Optional[str], bytes]] = []
    for f in (files or [])[: aura_guard.AURA_MAX_UPLOADS + 1]:
        raw.append((f.filename or "", f.content_type, await f.read(max_bytes + 1)))
    return await run_in_threadpool(aura_agent.stage_uploads, sess, raw)


@app.delete("/v1/agent/uploads")
def agent_uploads_clear(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    token = _token_from_request(request, authorization, x_session_token)
    sess = auth_acl.get_session(token) if token else None
    return aura_agent.clear_uploads(sess)


@app.get("/")
def root():
    if (WEB_DIR / "login.html").exists():
        return RedirectResponse(url="/login.html")
    return {
        "service": "suffai-command-center",
        "endpoints": [
            "/health",
            "/v1/metrics",
            "POST /v1/ads-lab/extract",
            "POST /v1/ads-lab/campaign",
            "GET /v1/ads-lab/campaigns",
            "POST /v1/ads-lab/campaigns/{id}/start",
            "POST /v1/ads-lab/campaigns/{id}/ignore",
            "PATCH /v1/ads-lab/campaigns/{id}/config",
            "POST /v1/ads-lab/campaigns/{id}/estimate",
            "POST /v1/ads-lab/campaigns/{id}/launch",
            "GET /v1/ads-lab/campaigns/{id}/monitor",
            "POST /v1/ads-lab/campaigns/{id}/budget-recommendation",
            "POST /v1/ads-lab/campaigns/{id}/export",
            "POST /v1/ads-lab/campaign-jobs",
            "GET /v1/ads-lab/campaign-jobs",
            "GET /v1/ads-lab/campaign-jobs/{id}",
            "POST /v1/chat",
            "POST /v1/agent/chat",
            "POST /v1/agent/uploads",
            "DELETE /v1/agent/uploads",
            "POST /v1/geo/brief",
            "GET /v1/admin/unique-summary",
            "GET /v1/admin/unique-garments",
            "GET /v1/admin/campaigns",
            "GET /v1/style-assistant/users",
            "GET /v1/style-assistant/brands",
            "POST /v1/style-assistant/recommend",
            "GET /v1/style-assistant/feed",
            "POST /v1/auth/login",
            "GET /v1/auth/me",
            "POST /v1/auth/logout",
        ],
    }


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")
