from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from api.deps import get_garment_store, require_user
from suffai.api.envelope import err, ok

router = APIRouter(prefix="/v1/brands", tags=["brands"])


@router.get("/{brand_id}/benchmarks")
def brand_benchmarks(
    brand_id: str,
    user: Dict[str, Any] = Depends(require_user),
):
    # Tenant scope: brand analysts only see their brand + approved aggregates
    if user.get("brand_id") and user["brand_id"] != brand_id and brand_id not in {
        user.get("brand", "").lower().replace(" ", ""),
        user.get("brand_id"),
    }:
        # Allow path brand_id matching display name slug OR exact brand_id claim
        claimed = (user.get("brand") or "").lower().replace(" ", "").replace("'", "").replace("&", "and")
        if brand_id not in {claimed, user.get("brand_id")}:
            return err("FORBIDDEN", "Tenant cannot access another brand's private metrics")

    store = get_garment_store()
    coverage = store.brand_coverage_summary()
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

    verified = store.list_verified_brand_observations()
    by_brand: Dict[str, int] = {}
    for g in verified:
        name = g.get("brand_name_raw") or g.get("brand_id") or "unknown"
        by_brand[name] = by_brand.get(name, 0) + 1
    total = sum(by_brand.values()) or 1
    benches = [
        {
            "brand": b,
            "observed_garments": n,
            "share_of_verified_pct": round(100.0 * n / total, 2),
            "is_requested": b == user.get("brand"),
        }
        for b, n in sorted(by_brand.items(), key=lambda x: -x[1])
    ]
    return ok({"brand_id": brand_id, "benchmarks": benches, "coverage": coverage})
