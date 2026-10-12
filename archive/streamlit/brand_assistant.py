"""Brand chatbot grounded in geo / competitor / product-research / campaign tables."""

from __future__ import annotations

import re

import pandas as pd


def answer_brand_query(
    question: str,
    *,
    my_brand: str,
    zone_df: pd.DataFrame,
    brand_bench: pd.DataFrame,
    gap_df: pd.DataFrame,
    product_rec: dict,
    campaign_ranked: pd.DataFrame,
    demo_df: pd.DataFrame,
) -> str:
    q = (question or "").strip().lower()
    if not q:
        return (
            "Ask about a zone (e.g. HSR), competitors, product research, "
            "or campaign ranking for your creative."
        )

    pin = re.search(r"560\d{3}", q)
    if pin or any(w in q for w in ["zone", "geo", "map", "density", "area", "pincode", "hub"]):
        if zone_df.empty:
            return "Geo table is empty for current filters."
        row = zone_df.iloc[0]
        if pin:
            hit = zone_df[zone_df["pincode"].astype(str) == pin.group(0)]
            if not hit.empty:
                row = hit.iloc[0]
        else:
            for _, z in zone_df.iterrows():
                if str(z["area"]).lower() in q:
                    row = z
                    break
        return (
            f"**{row['area']} ({row['pincode']})** — {int(row['customers'])} customers, "
            f"₹{row['revenue']:,.0f} spend. Trending: **{row['trending_commodity']}**, "
            f"size **{row['top_size']}**, color **{row['top_color']}**, "
            f"material **{row['top_material']}**. Segments: {row['segment_mix']}."
        )

    if any(w in q for w in ["competitor", "vs", "share of wallet", "benchmark", "gap"]):
        if brand_bench.empty:
            return "Competitor benchmark unavailable."
        me = brand_bench[brand_bench["brand"] == my_brand]
        leader = brand_bench.iloc[0]
        my_line = (
            f"Your rank is **#{int(me.iloc[0]['rank'])}** with "
            f"**{me.iloc[0]['share_of_wallet_pct']}%** share."
            if not me.empty
            else "You are not visible in the filtered wardrobe set."
        )
        gap_line = ""
        if not gap_df.empty:
            g = gap_df.iloc[0]
            gap_line = f" Biggest gap zone: **{g['area']}** vs {g['zone_leader']} ({g['share_gap_pct']} pts)."
        return (
            f"Market leader: **{leader['brand']}** ({leader['share_of_wallet_pct']}% SoW). "
            f"{my_line}{gap_line}"
        )

    if any(w in q for w in ["product research", "new product", "launch", "sku", "material", "what should i build"]):
        if not product_rec.get("ok"):
            return product_rec.get("message", "Product research not ready.")
        colors = ", ".join(product_rec.get("top_colors") or [])
        return (
            f"**New product brief for {my_brand}** ({product_rec.get('focus_area')}): "
            f"commodity **{product_rec['preferred_commodity']}**, "
            f"start size **{product_rec['preferred_size']}**, "
            f"material **{product_rec['preferred_material']}**, "
            f"colors **{colors}**. {product_rec.get('rationale', '')}"
        )

    if any(w in q for w in ["campaign", "creative", "ad rank", "ranking", "upload", "flight"]):
        if campaign_ranked.empty:
            return "Upload a creative in the Campaign tab to rank geo × segment fit."
        top = campaign_ranked.iloc[0]
        return (
            f"Top campaign slot: **{top['area']}** × **{top['persona']}** "
            f"(score {top['campaign_fit_score']}). Why: {top['why']}."
        )

    if any(w in q for w in ["demo", "demographic", "gender", "persona", "segment %"]):
        if demo_df.empty and zone_df.empty:
            return "No demographic breakdown for filters."
        if not zone_df.empty:
            row = zone_df.iloc[0]
            return (
                f"In **{row['area']}**: Champion {row['champion_pct']}%, "
                f"High Spender {row['high_spender_pct']}%, "
                f"Loyal {row['loyal_pct']}%, Steady {row['steady_pct']}%. "
                f"Gender cue — male {row['male_pct']:.0f}% / female {row['female_pct']:.0f}%."
            )
        top = demo_df.iloc[0]
        return f"Largest demo cell: {top['area']} · {top['persona']} · {top['gender']} ({top['pct_of_zone']}% of zone)."

    # default overview
    z_n = len(zone_df)
    b_n = len(brand_bench)
    return (
        f"**{my_brand} desk:** {z_n} zones, {b_n} brands in view. "
        f"Try: “density in HSR”, “competitor gaps”, “new product brief”, or “best campaign slot”."
    )
