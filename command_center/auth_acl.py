"""Role-based login: BQ login_cred_db + in-memory sessions."""

from __future__ import annotations

import csv
import hashlib
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("command_center.auth_acl")

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CSV = REPO / "command_center_data" / "auth" / "login_cred_db.csv"

ROLE_TABS: dict[str, list[str]] = {
    "brand": ["geo", "ads"],
    "customer": ["style"],
    "admin": ["geo", "ads", "style", "admin"],
}

SESSION_TTL_SEC = int(os.getenv("AUTH_SESSION_TTL_SEC", str(12 * 3600)))
_SESSIONS: dict[str, dict[str, Any]] = {}


def sha256_hex(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def tabs_for_role(role: str) -> list[str]:
    return list(ROLE_TABS.get(role, []))


def _session_public(sess: dict[str, Any]) -> dict[str, Any]:
    role = sess["role"]
    return {
        "token": sess["token"],
        "role": role,
        "username": sess["username"],
        "brand_name": sess.get("brand_name") or None,
        "customer_id": sess.get("customer_id") or None,
        "display_name": sess.get("display_name") or sess["username"],
        "allowed_tabs": tabs_for_role(role),
    }


def create_session(user: dict[str, Any]) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    sess = {
        "token": token,
        "role": user["role"],
        "username": user["username"],
        "brand_name": user.get("brand_name") or "",
        "customer_id": user.get("customer_id") or "",
        "display_name": user.get("display_name") or user["username"],
        "expires_at": time.time() + SESSION_TTL_SEC,
    }
    _SESSIONS[token] = sess
    return _session_public(sess)


def get_session(token: Optional[str]) -> Optional[dict[str, Any]]:
    if not token:
        return None
    sess = _SESSIONS.get(token)
    if not sess:
        return None
    if time.time() > float(sess.get("expires_at") or 0):
        _SESSIONS.pop(token, None)
        return None
    return _session_public(sess)


def destroy_session(token: Optional[str]) -> None:
    if token:
        _SESSIONS.pop(token, None)


def lookup_user_from_csv(username: str, password_hash: str) -> Optional[dict[str, Any]]:
    path = Path(os.getenv("LOGIN_CREDS_CSV") or DEFAULT_CSV)
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("username") or "").strip() != username:
                continue
            active = str(row.get("active") or "true").strip().lower()
            if active in {"0", "false", "no"}:
                return None
            if (row.get("password_hash") or "").strip().lower() != password_hash.lower():
                return None
            role = (row.get("role") or "").strip().lower()
            if role not in ROLE_TABS:
                return None
            return {
                "username": username,
                "role": role,
                "brand_name": (row.get("brand_name") or "").strip(),
                "customer_id": (row.get("customer_id") or "").strip(),
                "display_name": (row.get("display_name") or username).strip(),
            }
    return None


def lookup_user_from_bq(username: str, password_hash: str) -> Optional[dict[str, Any]]:
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
    dataset = os.getenv("BQ_DATASET", "suffai_ops")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")

    from google.cloud import bigquery  # type: ignore

    client = bigquery.Client(project=project)
    sql = f"""
    SELECT username, role, brand_name, customer_id, display_name, active
    FROM `{project}.{dataset}.login_cred_db`
    WHERE username = @username
      AND password_hash = @password_hash
      AND IFNULL(active, TRUE) = TRUE
    LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("username", "STRING", username),
            bigquery.ScalarQueryParameter("password_hash", "STRING", password_hash),
        ]
    )
    rows = list(client.query(sql, job_config=job_config).result())
    if not rows:
        return None
    r = rows[0]
    role = str(r["role"] or "").strip().lower()
    if role not in ROLE_TABS:
        return None
    return {
        "username": str(r["username"]),
        "role": role,
        "brand_name": str(r["brand_name"] or "").strip(),
        "customer_id": str(r["customer_id"] or "").strip(),
        "display_name": str(r["display_name"] or r["username"] or "").strip(),
    }


def authenticate(username: str, password: str) -> dict[str, Any]:
    """Verify credentials; prefer BQ, fall back to local CSV."""
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("username and password required")
    password_hash = sha256_hex(password)

    source = (os.getenv("AUTH_SOURCE") or "auto").strip().lower()
    user: Optional[dict[str, Any]] = None
    errors: list[str] = []

    if source in {"auto", "bq"}:
        try:
            user = lookup_user_from_bq(username, password_hash)
        except Exception as exc:
            log.warning("BQ auth lookup failed: %s", exc)
            errors.append(str(exc))
            if source == "bq":
                raise

    if user is None and source in {"auto", "csv"}:
        user = lookup_user_from_csv(username, password_hash)

    if user is None:
        raise PermissionError("Invalid username or password")

    return create_session(user)


def mask_metrics_for_brand(payload: dict[str, Any], brand_name: str) -> dict[str, Any]:
    """Rewrite other brands to 'Competitors' for brand-role sessions."""
    if not brand_name:
        return payload
    own = brand_name.strip().lower()
    out = dict(payload)

    def mask_brand_val(v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            return s
        if s.lower() == own:
            return brand_name  # preserve original casing of own brand if possible
        return "Competitors"

    # filters.brands list
    filters = dict(out.get("filters") or {}) if isinstance(out.get("filters"), dict) else {}
    brands = filters.get("brands")
    if isinstance(brands, list):
        filters["brands"] = [brand_name, "Competitors"]
    out["filters"] = filters

    # garment_cells / garment_segment_cells
    for key in ("garment_cells", "garment_segment_cells", "customer_cells"):
        cells = out.get(key)
        if not isinstance(cells, list):
            continue
        rewritten = []
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            c = dict(cell)
            if "brand" in c:
                c["brand"] = mask_brand_val(c.get("brand"))
            # nested brand distributions
            for dist_key in ("brands", "brand_counts", "brand_distribution"):
                arr = c.get(dist_key)
                if not isinstance(arr, list):
                    continue
                merged: dict[str, float] = {}
                for item in arr:
                    if isinstance(item, dict):
                        val = mask_brand_val(item.get("value") or item.get("name") or item.get("brand"))
                        cnt = float(item.get("count") or item.get("garments") or 0)
                    else:
                        val = mask_brand_val(item)
                        cnt = 1.0
                    merged[val] = merged.get(val, 0.0) + cnt
                c[dist_key] = [
                    {"value": k, "count": v} for k, v in sorted(merged.items(), key=lambda x: -x[1])
                ]
            rewritten.append(c)
        out[key] = rewritten

    return out
