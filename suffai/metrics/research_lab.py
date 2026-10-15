"""Product research lab: 3D silhouette + attribute dossier per ranked opportunity."""

from __future__ import annotations

from typing import Any

from suffai import PRIVACY_K
from suffai.metrics.command_center import LIVE_GARMENT_STATUSES, garment_profile

SILHOUETTE_KEYWORDS = (
    ("hoodie", ("jacket", "hoodie", "sweat", "coat", "blazer", "winter", "sweater", "pullover")),
    ("dress", ("saree", "sari", "lehenga", "dress", "gown", "kurta", "kurti", "anarkali")),
    ("pants", ("bottom", "pant", "trouser", "jean", "jogger", "chino", "short", "skirt", "legging")),
    ("tee", ("top", "shirt", "tee", "polo", "blouse", "tank", "crop")),
)


def silhouette_for(name: Any) -> str:
    text = str(name or "").lower().replace("-", "")
    for silhouette, words in SILHOUETTE_KEYWORDS:
        if any(w in text for w in words):
            return silhouette
    return "generic"


def research_lab(
    opportunities: list[dict[str, Any]],
    garments: list[dict[str, Any]],
    *,
    my_brand: str = "",
    limit: int = 6,
    k: int = PRIVACY_K,
) -> dict[str, Any]:
    live = [g for g in garments if not g.get("deleted_at") and g.get("review_status") in LIVE_GARMENT_STATUSES]
    by_silhouette: dict[str, list[dict[str, Any]]] = {}
    for g in live:
        by_silhouette.setdefault(silhouette_for(g.get("commodity_type")), []).append(g)

    items = []
    for opp in opportunities[:limit]:
        silhouette = silhouette_for(opp.get("commodity"))
        items.append({
            "rank": opp.get("rank"),
            "commodity": opp.get("commodity"),
            "silhouette": silhouette,
            "opportunity_score": opp.get("opportunity_score"),
            "zones_touched": opp.get("zones_touched"),
            "support_quantity": opp.get("support_quantity"),
            "confidence": opp.get("confidence"),
            "garments": garment_profile(by_silhouette.get(silhouette, []), my_brand=my_brand, k=k),
        })
    return {
        "items": items,
        "overall": garment_profile(live, my_brand=my_brand, k=k),
        "privacy_k": k,
        "note": (
            "Silhouette, size, colour and fabric come from Vertex garment extractions matched to the "
            "commodity's silhouette family; demand index is the opportunity score."
        ),
    }
