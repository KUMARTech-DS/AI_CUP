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
import style_assistant as style_asst  # noqa: E402

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


CHAT_SYSTEM = (
    "You are the SuffAI Command Center assistant for brand analysts. "
    "Answer only from the provided dashboard context (metrics.json summary, "
    "current Geo/Ads Lab UI state, and optional filter_context for PIN/age/"
    "gender/RFM/brand). "
    "Do not invent metrics, customers, or scores that are not in the context. "
    "If something is missing, say what filter or data would help. "
    "Admin / wardrobe / similarity data is out of scope. Be concise and practical."
)

GEO_BRIEF_SYSTEM = """You are a senior retail location strategist for SuffAI Command Center.

You receive ONLY aggregated, PII-free JSON about one focus PIN (pincode hub) and comparison hubs.
The map centroid is FIXED at the pincode hub — do not suggest moving the pin. Demographic
filters (age, gender, RFM, etc.) change estimated segment footfall and competitive mix only.

========================
YOUR JOB
========================
1) Decide whether the brand should OPEN A STORE at focus_pin: exactly one of Yes | Consider | No.
2) Explain the decision clearly for a brand marketer (not a data scientist).
3) Write a short market brief they can act on.

A centroid existing is NOT a reason to open. You must weigh demand, estimated footfall,
brand-vs-competitor wardrobe share at that PIN under the selected filters, and the brand's
commodity mix.

========================
HOW TO DECIDE (use the numbers in JSON; do not invent)
========================
Primary signals on focus_pin:
- demand: filtered customer count in the PIN for the selected demo filters
- est_footfall: Estimated segment footfall (always call it Estimated)
- brand_garments / competitor_garments / brand_share: wardrobe garment observations
  for analysis_brand vs everyone else (or "Competitors") in that PIN under the same filters
- commodities: top {commodity, count} rows for analysis_brand in that PIN (assortment proof)
- catchment_pop: coarse population buffer (secondary only)
- heuristic_decision / score: a weak prior from rules — you may override it if the
  competitive + commodity evidence disagrees, but say why

Guidance (adapt to sparse demo data; do not require huge absolute counts):
- Yes: meaningful filtered demand AND brand_share clearly competitive (often ≥ ~0.30–0.35)
  AND brand_garments not tiny AND commodities show a coherent assortment story for the filters
- Consider: mixed signals (decent demand but weak share, or strong share but thin footfall,
  or commodities concentrated in one weak category)
- No: near-zero demand/footfall for the filter slice, OR brand_garments ~0, OR brand_share
  dominated by competitors with no commodity foothold

If analysis_brand is missing or brand_garments+competitor_garments are all zero, choose No
or Consider and explain that competitive evidence is insufficient — never invent shares.

========================
EXPLANATION REQUIREMENTS
========================
In "explanation" (4–8 sentences or short bullets), cover:
1) Audience slice (filters: PIN, age, gender, RFM) and Estimated footfall / demand
2) Brand vs competitor balance at this PIN (cite brand_share and garment counts)
3) What commodities the brand shows in wardrobe data (cite top 2–3 from commodities)
4) Explicit open-store call: Yes / Consider / No and the single strongest reason
5) One practical next step (e.g. test assortment, compare top_pins, widen filters)

Never mention individual people, phones, emails, addresses, or exact personal coordinates.
Use area_label + PIN only. Do not invent PINs, counts, or percentages not present in JSON.
If a field is null/missing, say the evidence is incomplete.

========================
OUTPUT FORMAT (STRICT)
========================
Return ONLY valid JSON (no markdown fences, no preamble) with this schema:
{
  "decision": "Yes" | "Consider" | "No",
  "confidence": "high" | "medium" | "low",
  "explanation": "string — full decision rationale as specified above",
  "key_factors": ["short factor 1", "short factor 2", "short factor 3"],
  "brief": "string — 90-160 word market brief summarizing audience, competition, commodities, and the store action"
}
"""

CHAT_MAX_MESSAGE_CHARS = int(os.getenv("CHAT_MAX_MESSAGE_CHARS", "2000"))
CHAT_MAX_CONTEXT_CHARS = int(os.getenv("CHAT_MAX_CONTEXT_CHARS", "24000"))
GEO_BRIEF_MAX_CHARS = int(os.getenv("GEO_BRIEF_MAX_CHARS", "12000"))
METRICS_JSON = WEB_DIR / "metrics.json"


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    # From legacy api/routers/chat.py — optional explicit filters (PIN/age/gender/RFM/brand)
    filter_context: Optional[dict[str, Any]] = None


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


def _slim_metrics_json() -> dict[str, Any]:
    """Build a small summary from web/metrics.json for the chat model."""
    if not METRICS_JSON.is_file():
        return {"error": "metrics.json missing", "path": str(METRICS_JSON)}
    try:
        raw = json.loads(METRICS_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        log.exception("Failed to read metrics.json")
        return {"error": f"metrics.json read failed: {exc}"}

    customers = raw.get("customer_cells") or []
    garments = raw.get("garment_cells") or []
    pin_geo = raw.get("pin_geo") or {}

    total_customers = sum(int(c.get("customers") or 0) for c in customers if isinstance(c, dict))
    total_orders = sum(int(c.get("orders") or 0) for c in customers if isinstance(c, dict))
    total_revenue = sum(float(c.get("revenue") or 0) for c in customers if isinstance(c, dict))
    total_garments = sum(int(g.get("garments") or 0) for g in garments if isinstance(g, dict))

    brand_counts: dict[str, int] = {}
    for g in garments:
        if not isinstance(g, dict):
            continue
        b = g.get("brand") or "unknown"
        brand_counts[str(b)] = brand_counts.get(str(b), 0) + int(g.get("garments") or 0)
    top_brands = sorted(brand_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    pin_labels = []
    for pin, geo in list(pin_geo.items())[:20]:
        if isinstance(geo, dict):
            pin_labels.append(
                {
                    "pin": pin,
                    "area_label": geo.get("area_label"),
                    "population_pin": geo.get("population_pin"),
                }
            )

    return {
        "source": "web/metrics.json",
        "generated_at": raw.get("generated_at"),
        "filters": raw.get("filters") if isinstance(raw.get("filters"), dict) else {},
        "totals": {
            "customers": total_customers,
            "orders": total_orders,
            "revenue": round(total_revenue, 2),
            "garments": total_garments,
            "customer_cells": len(customers),
            "garment_cells": len(garments),
        },
        "top_brands": [{"brand": b, "garments": n} for b, n in top_brands],
        "pins": pin_labels,
        "extraction_jobs_count": raw.get("extraction_jobs_count"),
    }


def _sanitize_ui_context(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {"active_tab": raw.get("active_tab")}
    geo = raw.get("geo")
    if isinstance(geo, dict):
        out["geo"] = {
            "filters": geo.get("filters") if isinstance(geo.get("filters"), dict) else {},
            "summary": geo.get("summary") if isinstance(geo.get("summary"), dict) else {},
        }
    ads = raw.get("ads")
    if isinstance(ads, dict):
        ranked = ads.get("ranked") if isinstance(ads.get("ranked"), list) else []
        slim = []
        for row in ranked[:20]:
            if not isinstance(row, dict):
                continue
            slim.append(
                {
                    "filename": row.get("filename"),
                    "rank": row.get("rank"),
                    "score": row.get("score"),
                    "attrs": row.get("attrs") if isinstance(row.get("attrs"), dict) else {},
                    "reason": row.get("reason"),
                    "ok": row.get("ok"),
                }
            )
        out["ads"] = {
            "filters": ads.get("filters") if isinstance(ads.get("filters"), dict) else {},
            "filters_label": ads.get("filters_label") or "",
            "ranked": slim,
        }
    return out


def _sanitize_filter_context(raw: Any) -> dict[str, Any]:
    """Allowlist explicit audience filters (legacy chat filter_context)."""
    if not isinstance(raw, dict):
        return {}
    allowed = (
        "pin_code",
        "pin_codes",
        "age_group",
        "gender",
        "rfm_segment",
        "brand",
        "brand_name",
        "active_tab",
    )
    out: dict[str, Any] = {}
    for k in allowed:
        if k not in raw:
            continue
        v = raw[k]
        if v is None or v == "":
            continue
        if k == "pin_codes" and isinstance(v, list):
            out[k] = [str(p).strip() for p in v if str(p).strip()][:50]
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
    return out


# Keys that must never be forwarded to Gemini (PII / raw identity / precise personal geo)
_PII_KEY_DENY = frozenset(
    {
        "name",
        "display_name",
        "username",
        "email",
        "phone",
        "number",
        "address",
        "customer_id",
        "user_uuid",
        "_id",
        "id",
        "token",
        "password",
        "password_hash",
        "lat",
        "lon",
        "centroid_lat",
        "centroid_lon",
    }
)


def _strip_pii_keys(obj: Any, depth: int = 0) -> Any:
    """Recursively drop denied keys; keep only aggregates safe for prompts."""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = str(k).strip().lower()
            if key in _PII_KEY_DENY:
                continue
            if any(x in key for x in ("email", "phone", "address", "password", "token")):
                continue
            cleaned = _strip_pii_keys(v, depth + 1)
            if cleaned is not None:
                out[str(k)] = cleaned
        return out
    if isinstance(obj, list):
        return [_strip_pii_keys(x, depth + 1) for x in obj[:40]]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)[:200]


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


def _call_vertex_chat(
    message: str,
    context: dict[str, Any],
    request_id: str,
    *,
    system: Optional[str] = None,
    max_context_chars: Optional[int] = None,
) -> str:
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
    location = os.getenv("VERTEX_LOCATION", "asia-south1")
    model_name = os.getenv("VERTEX_CHAT_MODEL", "gemini-2.0-flash")
    if not project:
        raise HTTPException(status_code=503, detail="GOOGLE_CLOUD_PROJECT is not set")

    try:
        import vertexai  # type: ignore
        from vertexai.generative_models import GenerativeModel  # type: ignore
    except ImportError as exc:
        log.exception("request_id=%s missing google-cloud-aiplatform", request_id)
        raise HTTPException(status_code=503, detail=f"Missing Vertex libs: {exc}") from exc

    cap = max_context_chars if max_context_chars is not None else CHAT_MAX_CONTEXT_CHARS
    context_json = json.dumps(context, ensure_ascii=False, default=str)
    if len(context_json) > cap:
        context_json = context_json[:cap] + "…[truncated]"

    sys_prompt = system or CHAT_SYSTEM
    prompt = (
        f"{sys_prompt}\n\n"
        f"Dashboard context (JSON):\n{context_json}\n\n"
        f"User question:\n{message}"
    )

    try:
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
    except HTTPException:
        raise
    except Exception as exc:
        log.exception(
            "request_id=%s Vertex chat failed project=%s location=%s model=%s",
            request_id,
            project,
            location,
            model_name,
        )
        raise HTTPException(status_code=503, detail=f"Vertex chat failed: {exc}") from exc


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


def _call_extraction(
    *,
    extraction_url: str,
    bucket: str,
    object_key: str,
    token: Optional[str],
) -> dict[str, Any]:
    import urllib.request

    idem = f"ads-lab-campaign-{uuid.uuid4().hex}"
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_compare_pins(
    *,
    similarity_url: str,
    bucket: str,
    object_key: str,
    pin_codes: list[str],
    token: Optional[str],
) -> dict[str, Any]:
    import urllib.request

    body = json.dumps(
        {
            "gcs_bucket": bucket,
            "gcs_object_key": object_key,
            "pin_codes": pin_codes,
            "topk": 5,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{similarity_url.rstrip('/')}/v1/similarity/compare-pins",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
    brand = (brand_name or "").strip()
    # Brand role: force own brand (cannot upload as another brand)
    token = _token_from_request(request, authorization, x_session_token)
    sess = auth_acl.get_session(token) if token else None
    if sess and sess.get("role") == "brand":
        forced = (sess.get("brand_name") or "").strip()
        if not forced:
            raise HTTPException(403, "Brand session missing brand_name")
        brand = forced
    elif sess and sess.get("role") == "customer":
        raise HTTPException(403, "Customers cannot run Ads Lab campaigns")

    pins = _parse_pin_codes(pin_codes)
    age = (age_group or "").strip() or None
    gen = (gender or "").strip() or None
    rfm = (rfm_segment or "").strip() or None

    if not brand:
        raise HTTPException(400, "brand_name is required")
    if not pins:
        raise HTTPException(400, "pin_codes required (one or more)")
    if not files:
        raise HTTPException(400, "files required")

    extraction_url = (os.getenv("EXTRACTION_URL") or "").rstrip("/")
    similarity_url = (os.getenv("SIMILARITY_URL") or "").rstrip("/")
    bucket = os.getenv("GCS_IMAGE_BUCKET") or ""
    if not extraction_url:
        raise HTTPException(503, "EXTRACTION_URL not set")
    if not similarity_url:
        raise HTTPException(503, "SIMILARITY_URL not set")
    if not bucket:
        raise HTTPException(503, "GCS_IMAGE_BUCKET not set")
    if not os.getenv("DATABASE_URL", "").strip():
        raise HTTPException(503, "DATABASE_URL not set (brand_campaigns tables)")

    try:
        from google.cloud import storage  # type: ignore
        import google.auth.transport.requests  # type: ignore
        import google.oauth2.id_token  # type: ignore
    except ImportError as exc:
        raise HTTPException(503, f"Missing GCP libs: {exc}") from exc

    try:
        client = storage.Client()
        bkt = client.bucket(bucket)
    except Exception as exc:
        raise HTTPException(503, f"GCS unavailable: {exc}") from exc

    auth_req = google.auth.transport.requests.Request()

    def _id_token(audience: str) -> Optional[str]:
        try:
            return google.oauth2.id_token.fetch_id_token(auth_req, audience)
        except Exception:
            log.warning("Could not fetch ID token for %s", audience)
            return None

    extract_token = _id_token(extraction_url)
    sim_token = _id_token(similarity_url)

    try:
        metrics = compute_metrics()
    except HTTPException:
        # Fall back to metrics.json for attr_fit if BQ down
        if METRICS_JSON.is_file():
            metrics = json.loads(METRICS_JSON.read_text(encoding="utf-8"))
        else:
            raise

    profile = brand_camp.build_segment_profile(
        metrics,
        pin_codes=pins,
        age_group=age,
        gender=gen,
        rfm_segment=rfm,
    )

    try:
        campaign_id = brand_camp.create_campaign(
            brand_name=brand,
            pin_codes=pins,
            age_group=age,
            gender=gen,
            rfm_segment=rfm,
        )
    except Exception as exc:
        log.exception("create_campaign failed")
        raise HTTPException(503, f"brand_campaigns DB failed: {exc}") from exc

    slug = brand_camp.brand_slug(brand)
    ranked_items: list[dict[str, Any]] = []

    for f in files:
        if not f.content_type or not f.content_type.startswith("image/"):
            continue
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(
            f.content_type, "jpg"
        )
        sku_uuid = str(uuid.uuid4())
        object_key = f"brands/{slug}/{campaign_id}/{sku_uuid}.{ext}"
        data = await f.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(400, f"{f.filename} too large (max 10MB)")

        try:
            blob = bkt.blob(object_key)
            blob.upload_from_string(data, content_type=f.content_type)
        except Exception as exc:
            log.exception("GCS upload failed key=%s", object_key)
            ranked_items.append(
                {
                    "sku_uuid": sku_uuid,
                    "filename": f.filename,
                    "ok": False,
                    "error": f"GCS upload failed: {exc}",
                    "rank_score": None,
                }
            )
            continue

        attrs: dict[str, Any] = {}
        raw_result: Any = {}
        extract_ok = False
        extract_error = None
        try:
            payload = _call_extraction(
                extraction_url=extraction_url,
                bucket=bucket,
                object_key=object_key,
                token=extract_token,
            )
            raw_result = payload.get("result") or {}
            attrs = {
                "color": raw_result.get("color") or raw_result.get("dominant_color"),
                "brand": brand,  # overwrite extraction brand
                "size": raw_result.get("size"),
                "material": raw_result.get("material"),
                "pattern": raw_result.get("pattern"),
                "sleeve_type": raw_result.get("sleeve_type") or raw_result.get("sleeve"),
                "collar_type": raw_result.get("collar_type") or raw_result.get("collar"),
                "name": raw_result.get("garment_type")
                or raw_result.get("name")
                or raw_result.get("category"),
                "confidence": raw_result.get("confidence")
                or raw_result.get("confidence_score"),
            }
            extract_ok = True
        except Exception as exc:
            log.exception("Extract failed sku=%s", sku_uuid)
            extract_error = str(exc)
            attrs = {"brand": brand}

        try:
            brand_camp.insert_sku(
                sku_uuid=sku_uuid,
                campaign_id=campaign_id,
                brand_name=brand,
                original_filename=f.filename,
                gcs_bucket=bucket,
                gcs_object_key=object_key,
                attributes=attrs,
                raw_result=raw_result,
                extract_ok=extract_ok,
                extract_error=extract_error,
            )
        except Exception as exc:
            log.exception("insert_sku failed")
            raise HTTPException(503, f"brand_skus insert failed: {exc}") from exc

        sim_fit = 0.0
        freq_cov = 0.0
        per_pin: dict[str, Any] = {}
        compare_err = None
        try:
            cmp = _call_compare_pins(
                similarity_url=similarity_url,
                bucket=bucket,
                object_key=object_key,
                pin_codes=pins,
                token=sim_token,
            )
            sim_fit = float(cmp.get("aggregate_fit") or 0)
            per_pin = cmp.get("per_pin") or {}
            freq_cov = brand_camp.freq_coverage_from_compare(per_pin)
            emb = cmp.get("embedding")
            if isinstance(emb, list) and emb:
                brand_camp.update_sku_embedding(
                    sku_uuid,
                    emb,
                    str(cmp.get("embedding_model") or ""),
                    int(cmp.get("embedding_dim") or len(emb)),
                )
            brand_camp.upsert_pin_scores(sku_uuid, per_pin)
        except Exception as exc:
            log.exception("compare-pins failed sku=%s", sku_uuid)
            compare_err = str(exc)

        a_fit = brand_camp.attr_fit_score(attrs, profile)
        rank_score = brand_camp.blend_rank_score(
            sim_fit=sim_fit, freq_coverage=freq_cov, attr_fit=a_fit
        )
        ranked_items.append(
            {
                "sku_uuid": sku_uuid,
                "filename": f.filename,
                "ok": extract_ok and compare_err is None,
                "gcs_object_key": object_key,
                "attributes": attrs,
                "extract_ok": extract_ok,
                "extract_error": extract_error,
                "compare_error": compare_err,
                "sim_fit": round(sim_fit, 4),
                "freq_coverage": round(freq_cov, 4),
                "attr_fit": round(a_fit, 4),
                "rank_score": rank_score,
                "per_pin": per_pin,
            }
        )

    ranked_items.sort(
        key=lambda r: (
            r.get("rank_score") is None,
            -(r.get("rank_score") or -1),
        )
    )
    for i, row in enumerate(ranked_items, start=1):
        row["rank"] = i

    try:
        brand_camp.set_campaign_status(campaign_id, "ranked")
    except Exception:
        log.exception("set_campaign_status failed")

    return {
        "ok": True,
        "campaign_id": campaign_id,
        "brand_name": brand,
        "filters": {
            "pin_codes": pins,
            "age_group": age,
            "gender": gen,
            "rfm_segment": rfm,
        },
        "profile_cell_count": profile.get("cell_count"),
        "items": ranked_items,
    }


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
def style_assistant_users(limit: int = Query(200, ge=1, le=500)):
    """Distinct customer ids from user_wardrobe for Style Assistant dropdown."""
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
    request_id = uuid.uuid4().hex[:12]
    token = _token_from_request(request, authorization, x_session_token)
    sess = auth_acl.get_session(token) if token else None
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
        raw = _call_vertex_chat(
            "Return the store-opening decision JSON now.",
            payload,
            request_id,
            system=GEO_BRIEF_SYSTEM,
            max_context_chars=GEO_BRIEF_MAX_CHARS,
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


@app.post("/v1/chat")
def chat(body: ChatRequest):
    """Dashboard Q&A from metrics.json summary + Geo/Ads UI filters."""
    request_id = uuid.uuid4().hex[:12]
    message = (body.message or "").strip()
    if not message:
        return JSONResponse(
            status_code=400,
            content={"detail": "message is required", "request_id": request_id},
        )
    if len(message) > CHAT_MAX_MESSAGE_CHARS:
        return JSONResponse(
            status_code=400,
            content={
                "detail": f"message too long (max {CHAT_MAX_MESSAGE_CHARS} chars)",
                "request_id": request_id,
            },
        )

    if not _env_bool("ENABLE_VERTEX", False):
        log.warning("request_id=%s chat rejected ENABLE_VERTEX=false", request_id)
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Chat disabled: set ENABLE_VERTEX=true on command-center",
                "request_id": request_id,
            },
        )

    ui_ctx = _sanitize_ui_context(body.context)
    filter_ctx = _sanitize_filter_context(body.filter_context)
    # Also accept filter_context nested under context (UI convenience)
    if not filter_ctx and isinstance(body.context, dict):
        filter_ctx = _sanitize_filter_context(body.context.get("filter_context"))
    metrics_summary = _slim_metrics_json()
    context = {
        "metrics_json": metrics_summary,
        "ui": ui_ctx,
        "filter_context": filter_ctx,
    }
    log.info(
        "request_id=%s chat start tab=%s metrics_ok=%s filters=%s msg_len=%s",
        request_id,
        ui_ctx.get("active_tab"),
        "error" not in metrics_summary,
        bool(filter_ctx),
        len(message),
    )

    try:
        answer = _call_vertex_chat(message, context, request_id)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail, "request_id": request_id},
        )

    log.info("request_id=%s chat ok answer_len=%s", request_id, len(answer))
    return {
        "ok": True,
        "answer": answer,
        "request_id": request_id,
        "model": os.getenv("VERTEX_CHAT_MODEL", "gemini-2.0-flash"),
    }


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
            "POST /v1/chat",
            "POST /v1/geo/brief",
            "GET /v1/admin/unique-summary",
            "GET /v1/admin/unique-garments",
            "GET /v1/style-assistant/users",
            "GET /v1/style-assistant/brands",
            "POST /v1/style-assistant/recommend",
            "POST /v1/auth/login",
            "GET /v1/auth/me",
            "POST /v1/auth/logout",
        ],
    }


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")
