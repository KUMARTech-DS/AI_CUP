from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.deps import (
    get_customer_index,
    get_garment_coverage,
    get_garment_store,
    get_verified_garments,
    require_user,
)
from suffai.api.envelope import err, ok
from suffai.metrics.brand import brand_benchmarks

router = APIRouter(prefix="/v1/brands", tags=["brands"])


class CatalogSkuBody(BaseModel):
    brand_sku: str
    category: Optional[str] = None
    material: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    price_band: Optional[str] = None


@router.get("/{brand_id}/benchmarks")
def brand_benchmarks_route(
    brand_id: str,
    geo_ids: Optional[str] = None,
    genders: Optional[str] = None,
    age_bands: Optional[str] = None,
    device_types: Optional[str] = None,
    user: Dict[str, Any] = Depends(require_user),
):
    claimed = (user.get("brand") or "").lower().replace(" ", "").replace("'", "").replace("&", "and")
    if brand_id not in {claimed, user.get("brand_id")}:
        return err("FORBIDDEN", "Tenant cannot access another brand's private metrics")

    coverage = get_garment_coverage()
    if not coverage["brand_analytics_available"]:
        return err(
            "INSUFFICIENT_DATA",
            coverage["empty_state"],
            data={
                "brand_id": brand_id,
                "tenant_brand": user.get("brand"),
                "benchmarks": [],
                "coverage": coverage,
                "note": (
                    "Competitive share is share of verified consented garment "
                    "observations — not laundry store volume or market sales."
                ),
            },
        )

    pins = [p.strip() for p in geo_ids.split(",")] if geo_ids else None
    gender_list = [g.strip() for g in genders.split(",")] if genders else None
    age_list = [a.strip() for a in age_bands.split(",")] if age_bands else None
    device_list = [d.strip() for d in device_types.split(",")] if device_types else None
    data = brand_benchmarks(
        get_verified_garments(),
        my_brand=str(user.get("brand") or ""),
        geo_ids=pins,
        customers_by_id=get_customer_index(),
        genders=gender_list,
        age_bands=age_list,
        device_types=device_list,
    )
    data["coverage"] = coverage
    data["brand_id"] = brand_id
    return ok(data)


@router.post("/{brand_id}/catalog")
def upsert_catalog(
    brand_id: str,
    body: CatalogSkuBody,
    user: Dict[str, Any] = Depends(require_user),
):
    claimed = (user.get("brand") or "").lower().replace(" ", "").replace("'", "").replace("&", "and")
    if brand_id not in {claimed, user.get("brand_id")}:
        return err("FORBIDDEN", "Tenant cannot modify another brand catalog")
    store = get_garment_store()
    sku = store.upsert_catalog_sku({
        "brand_id": brand_id,
        "brand_sku": body.brand_sku,
        "category": body.category,
        "material": body.material,
        "color": body.color,
        "size": body.size,
        "price_band": body.price_band,
    })
    return ok(sku)


@router.get("/{brand_id}/catalog")
def list_catalog(brand_id: str, user: Dict[str, Any] = Depends(require_user)):
    store = get_garment_store()
    return ok({"brand_id": brand_id, "skus": store.list_catalog(brand_id)})
