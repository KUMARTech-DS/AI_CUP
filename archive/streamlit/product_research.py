"""Product research: recommend attributes for a new SKU from brand + competitor gaps."""

from __future__ import annotations

import pandas as pd

from services.geo_intel import enrich_taste_with_material
from services.brand_analytics import brand_vs_competitors, attribute_split_for_brand


def recommend_new_product(
    taste_df: pd.DataFrame,
    my_brand: str,
    focus_area: str | None = None,
) -> dict:
    """
    Suggest preferred commodity, size, material, colors for a new product line.
    Uses market demand minus where my brand is already saturated.
    """
    taste_df = enrich_taste_with_material(taste_df)
    if taste_df.empty:
        return {"ok": False, "message": "No taste data available."}

    market = taste_df if not focus_area else taste_df[taste_df["area"] == focus_area]
    if market.empty:
        market = taste_df

    mine = market[market["brand"] == my_brand]
    others = market[market["brand"] != my_brand]

    def top_attrs(df: pd.DataFrame, col: str, n: int = 5) -> list[dict]:
        if df.empty or col not in df.columns:
            return []
        g = df.groupby(col, as_index=False).agg(demand=("customer_id", "nunique"), spend=("spend_share", "sum"))
        g = g.sort_values("spend", ascending=False).head(n)
        return g.to_dict("records")

    market_comm = top_attrs(market, "commodity")
    my_comm = set(mine["commodity"].unique()) if not mine.empty else set()

    # Prefer high-demand commodities where brand is under-indexed
    preferred_commodity = None
    for row in market_comm:
        if row["commodity"] not in my_comm:
            preferred_commodity = row["commodity"]
            break
    if preferred_commodity is None and market_comm:
        preferred_commodity = market_comm[0]["commodity"]

    focus = market[market["commodity"] == preferred_commodity] if preferred_commodity else market
    sizes = top_attrs(focus if not focus.empty else market, "size")
    colors = top_attrs(focus if not focus.empty else market, "color")
    materials = top_attrs(focus if not focus.empty else market, "material")

    bench = brand_vs_competitors(market, my_brand)
    my_rank = int(bench.loc[bench["brand"] == my_brand, "rank"].iloc[0]) if not bench.empty and (bench["brand"] == my_brand).any() else None
    leader = bench.iloc[0]["brand"] if not bench.empty else "—"

    rationale = (
        f"In {focus_area or 'all zones'}, market demand peaks on **{preferred_commodity}**. "
        f"Leader is **{leader}**. "
        + (
            f"Your brand rank is **#{my_rank}** — launch into under-served demand."
            if my_rank
            else "Launch into the highest-demand whitespace."
        )
    )

    return {
        "ok": True,
        "preferred_commodity": preferred_commodity or "Top Wear",
        "preferred_size": sizes[0]["size"] if sizes else "M",
        "preferred_material": materials[0]["material"] if materials else "Cotton",
        "top_colors": [c["color"] for c in colors[:3]] or ["Deep Navy", "Crisp White"],
        "size_ladder": sizes,
        "color_ladder": colors,
        "material_ladder": materials,
        "commodity_ladder": market_comm,
        "my_brand_attrs": attribute_split_for_brand(market, my_brand),
        "rationale": rationale,
        "focus_area": focus_area or "All zones",
    }
