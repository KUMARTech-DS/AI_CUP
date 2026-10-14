"""Customer profile API — customer_uuid joins garment image user_uuid."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.deps import get_customer_index, get_customer_store, get_normalized, require_user
from suffai.api.envelope import err, ok
from suffai.metrics.demographics import demographic_summary

router = APIRouter(prefix="/v1/customers", tags=["customers"])


class CustomerUpsertBody(BaseModel):
    customer_uuid: str
    user_id: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    pin: Optional[str] = None
    address: Optional[str] = None
    phone_number: Optional[str] = None
    device_type: Optional[str] = None
    customer_name: Optional[str] = None


class DemoSummaryBody(BaseModel):
    customer_uuids: List[str] = Field(default_factory=list)
    genders: Optional[List[str]] = None
    age_bands: Optional[List[str]] = None
    device_types: Optional[List[str]] = None


@router.post("/sync")
def sync_customers(user: Dict[str, Any] = Depends(require_user)):
    """Rebuild derived + Cloud SQL customers from offline normalize."""
    store = get_customer_store()
    n = store.sync_from_normalized(get_normalized().get("customers") or [])
    get_customer_index.cache_clear()
    return ok({"synced": n, "note": "customer_uuid maps to garment user_uuid / source_customer_id"})


@router.get("/{customer_uuid}")
def get_customer(customer_uuid: str, user: Dict[str, Any] = Depends(require_user)):
    store = get_customer_store()
    profile = store.public_profile(customer_uuid)
    if not profile:
        # Fall back to in-memory index (offline normalize)
        row = get_customer_index().get(customer_uuid)
        if not row:
            return err("NOT_FOUND", "customer not found")
        profile = {
            "customer_uuid": row.get("customer_uuid") or row.get("customer_id"),
            "user_id": row.get("user_id"),
            "gender": row.get("gender"),
            "age": row.get("age"),
            "pin": row.get("pin") or row.get("home_pin"),
            "device_type": row.get("device_type"),
        }
    return ok(profile)


@router.put("")
def upsert_customer(body: CustomerUpsertBody, user: Dict[str, Any] = Depends(require_user)):
    store = get_customer_store()
    rec = store.upsert(body.model_dump())
    get_customer_index.cache_clear()
    return ok(store.public_profile(rec["customer_uuid"]))


@router.post("/demographics")
def demographics(body: DemoSummaryBody, user: Dict[str, Any] = Depends(require_user)):
    index = get_customer_index()
    ids = body.customer_uuids or list({
        c.get("customer_uuid") or c.get("customer_id")
        for c in (get_normalized().get("customers") or [])
        if c.get("customer_uuid") or c.get("customer_id")
    })
    summary = demographic_summary(index, ids)
    return ok(summary)
