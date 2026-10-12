from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.deps import get_normalized, get_rfm
from suffai.api.envelope import ok
from suffai.metrics.geo import geo_active_customers, geo_query

router = APIRouter(prefix="/v1/geo", tags=["geo"])


class GeoQueryRequest(BaseModel):
    geo_level: str = "pin"
    geo_ids: Optional[List[str]] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    rfm_segments: Optional[List[str]] = None
    metrics: List[str] = Field(default_factory=lambda: ["active_customers", "commodity_popularity"])


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s).replace(tzinfo=datetime.now().astimezone().tzinfo)


@router.get("/tiles")
def tiles(metric: str = "active_customers"):
    norm = get_normalized()
    rows = geo_active_customers(norm["orders"])
    return ok({"metric": metric, "tiles": rows}, coverage={"tile_count": len(rows)})


@router.post("/query")
def query(body: GeoQueryRequest) -> Dict[str, Any]:
    norm = get_normalized()
    result = geo_query(
        norm["orders"],
        norm["order_commodity_lines"],
        get_rfm(),
        geo_ids=body.geo_ids,
        date_from=_parse_date(body.date_from),
        date_to=_parse_date(body.date_to),
        rfm_segments=body.rfm_segments,
    )
    return ok(result, coverage=result.get("meta"))
