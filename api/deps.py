"""Shared data bootstrap for Cloud Run API.

Data sources:
  * USE_BIGQUERY_MARTS=true → orders / customers / stores / garments from BigQuery
    (garments = suffai_marts.v_garment_enriched, streamed by suffai-extraction)
  * otherwise → source JSON snapshots (gs://GCS_RAW_BUCKET/GCS_DATA_PREFIX, or DATA_DIR locally)
Operational records (garments, campaigns, customers, …) live in Cloud SQL when DATABASE_URL is set.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from functools import lru_cache, wraps
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, Header, HTTPException

from config import settings
from services.auth_service import decode_access_token
from suffai.customers.store import CustomerStore, customer_index_from_rows
from suffai.garments.store import GarmentStore, coverage_summary, verified_brand_observations
from suffai.gcp_io import source_data_location
from suffai.ingest import load_source_collections, normalize_all
from suffai.metrics.rfm import compute_rfm_snapshots
from suffai.warehouse import repository

log = logging.getLogger("suffai.deps")

CORE_CACHE_SECONDS = int(os.getenv("BQ_CACHE_SECONDS", "900"))
GARMENT_CACHE_SECONDS = int(os.getenv("BQ_GARMENT_CACHE_SECONDS", "60"))


def _ttl_cache(seconds: int) -> Callable:
    """Cache a zero-arg function for `seconds`; exposes cache_clear() like lru_cache."""

    def deco(fn):
        lock = threading.Lock()
        state: Dict[str, Any] = {}

        @wraps(fn)
        def wrapper():
            with lock:
                if "value" in state and time.monotonic() - state["at"] < seconds:
                    return state["value"]
            value = fn()
            with lock:
                state.update(value=value, at=time.monotonic())
            return value

        wrapper.cache_clear = state.clear  # type: ignore[attr-defined]
        return wrapper

    return deco


@lru_cache(maxsize=1)
def _local_normalized() -> Dict[str, Any]:
    raw = load_source_collections(source_data_location())
    return normalize_all(raw)


@_ttl_cache(CORE_CACHE_SECONDS)
def get_normalized() -> Dict[str, Any]:
    local = _local_normalized()
    if not repository.enabled():
        return local
    try:
        return {**local, **repository.load_core()}
    except Exception as exc:
        log.warning("BigQuery core read failed, using local JSON: %s", exc)
        return local


@_ttl_cache(CORE_CACHE_SECONDS)
def get_rfm():
    norm = get_normalized()
    return compute_rfm_snapshots(norm["orders"], norm["stores"])


def get_garment_store() -> GarmentStore:
    return GarmentStore(settings.DERIVED_DIR)


def get_customer_store() -> CustomerStore:
    return CustomerStore(settings.DERIVED_DIR)


@_ttl_cache(CORE_CACHE_SECONDS)
def get_customer_index() -> Dict[str, Dict[str, Any]]:
    """customer_uuid (= garment user_uuid) → profile. Derived overrides win over the base source."""
    index = customer_index_from_rows(get_normalized().get("customers") or [])
    derived = get_customer_store().list_customers()
    if derived:
        index.update(customer_index_from_rows(derived))
    return index


@_ttl_cache(GARMENT_CACHE_SECONDS)
def _bq_garments() -> List[Dict[str, Any]]:
    return repository.load_garments()


def get_garments() -> List[Dict[str, Any]]:
    """All live garment observations shown on dashboards."""
    if repository.enabled():
        try:
            return _bq_garments()
        except Exception as exc:
            log.warning("BigQuery garment read failed, using local store: %s", exc)
    return get_garment_store().list_garment_instances()


def get_verified_garments() -> List[Dict[str, Any]]:
    return verified_brand_observations(get_garments())


def get_garment_coverage() -> Dict[str, Any]:
    return coverage_summary(get_garments())


def find_garment(garment_id: str) -> Optional[Dict[str, Any]]:
    for g in get_garments():
        if g.get("garment_instance_id") == garment_id or g.get("id") == garment_id:
            return g
    return None


def clear_garment_cache() -> None:
    _bq_garments.cache_clear()


def get_current_user(
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        if settings.DEMO_AUTH_ENABLED and settings.ENVIRONMENT == "local":
            # Local anonymous read for health/dev tools; protected routes still prefer token
            return {
                "brand": "Peter England",
                "brand_id": "peterengland",
                "tenant_id": "tenant-peterengland",
                "role": "brand_analyst",
                "sub": "dev@local",
            }
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
    token = authorization.split(" ", 1)[1].strip()
    claims = decode_access_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
    return claims


def require_user(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    return user
