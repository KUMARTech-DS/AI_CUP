"""Campaign creative ranking from uploaded image vs geo / demo / segment demand."""

from __future__ import annotations

from typing import Any

import pandas as pd
from PIL import Image

from services.product_intel import extract_dominant_color, extract_tag_ocr, lifecycle_score
from services.geo_intel import enrich_taste_with_material, zone_density_table


def analyze_creative(img: Image.Image) -> dict[str, Any]:
    color = extract_dominant_color(img)
    brand, size = extract_tag_ocr(img)
    life = lifecycle_score(img)
    return {
        "detected_color": color,
        "detected_brand": brand,
        "detected_size": size,
        "lifecycle_score": life,
    }


def rank_campaign_by_segments(
    taste_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    creative: dict[str, Any],
    commodity_hint: str = "Top Wear",
    brand_hint: str | None = None,
) -> pd.DataFrame:
    """
    Score each zone × persona for how well the creative attributes match demand.
    Higher = better place to run the campaign.
    """
    taste_df = enrich_taste_with_material(taste_df)
    if summary_df.empty:
        return pd.DataFrame()

    color = creative.get("detected_color")
    size = creative.get("detected_size")
    brand = brand_hint or creative.get("detected_brand")

    zones = zone_density_table(summary_df, taste_df)
    rows = []
    for _, z in zones.iterrows():
        area = z["area"]
        zt = taste_df[taste_df["area"] == area] if not taste_df.empty else pd.DataFrame()
        area_users = summary_df[summary_df["area"] == area]
        personas = area_users["persona"].value_counts(normalize=True) if not area_users.empty else pd.Series(dtype=float)

        for persona, p_share in (personas.items() if len(personas) else [("All", 1.0)]):
            pt = zt[zt["persona"] == persona] if not zt.empty and persona != "All" else zt
            if pt.empty and not zt.empty:
                pt = zt

            score = 35.0  # base
            reasons = []

            if not pt.empty and color:
                color_share = (pt["color"] == color).mean()
                score += color_share * 35
                if color_share > 0:
                    reasons.append(f"{color} affinity {color_share*100:.0f}%")

            if not pt.empty and size:
                size_share = (pt["size"] == size).mean()
                score += size_share * 15
                if size_share > 0:
                    reasons.append(f"size {size} {size_share*100:.0f}%")

            if not pt.empty and commodity_hint:
                comm_share = (pt["commodity"] == commodity_hint).mean()
                score += comm_share * 20
                if comm_share > 0:
                    reasons.append(f"{commodity_hint} demand {comm_share*100:.0f}%")

            if not pt.empty and brand:
                brand_share = (pt["brand"] == brand).mean()
                score += brand_share * 10
                if brand_share > 0:
                    reasons.append(f"{brand} presence {brand_share*100:.0f}%")

            # density bonus
            score += min(z["customers"] / 50.0, 10)

            rows.append({
                "area": area,
                "pincode": z["pincode"],
                "persona": persona,
                "segment_share_pct": round(float(p_share) * 100, 1),
                "customers_in_zone": int(z["customers"]),
                "campaign_fit_score": round(min(score, 99.0), 1),
                "trending_commodity": z["trending_commodity"],
                "zone_top_color": z["top_color"],
                "zone_top_size": z["top_size"],
                "why": "; ".join(reasons) if reasons else "General zone density fit",
            })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("campaign_fit_score", ascending=False)


def campaign_summary(ranked: pd.DataFrame, creative: dict[str, Any]) -> str:
    if ranked.empty:
        return "No segments to rank against."
    top = ranked.iloc[0]
    return (
        f"Best flight: **{top['area']}** × **{top['persona']}** "
        f"(score {top['campaign_fit_score']}). "
        f"Creative reads as **{creative.get('detected_color', 'unknown color')}**. "
        f"{top['why']}."
    )
