"""Lightweight Style Assistant — rule-based Q&A over in-memory insights."""

from __future__ import annotations

import re

import pandas as pd


def answer_query(
    question: str,
    summary_df: pd.DataFrame,
    taste_df: pd.DataFrame,
    pin_ads: pd.DataFrame,
    brand_df: pd.DataFrame,
) -> str:
    q = (question or "").strip().lower()
    if not q:
        return "Ask about champions, top brands, a pincode, or ad ideas."

    if any(w in q for w in ["champion", "persona", "segment", "rfm"]):
        if summary_df.empty:
            return "Customer summary is empty."
        counts = summary_df["persona"].value_counts()
        top = counts.index[0]
        return (
            f"**Persona mix:** {', '.join(f'{k} ({v})' for k, v in counts.head(4).items())}. "
            f"Largest segment is **{top}**. Use RFM Champions for premium 1P offers."
        )

    if "brand" in q or "competitor" in q:
        if brand_df.empty:
            return "Brand benchmark unavailable."
        top = brand_df.iloc[0]
        return (
            f"**Leading wardrobe brand:** {top['brand']} "
            f"({top['share_of_wallet_pct']}% share of wallet, {int(top['users'])} users). "
            f"Challenge them with lookalike creatives in high-velocity PINs."
        )

    pin_match = re.search(r"560\d{3}", q)
    if pin_match or "pincode" in q or "pin code" in q or "area" in q:
        if pin_ads.empty:
            return "No PIN-level ad plans yet."
        row = pin_ads.iloc[0]
        if pin_match:
            hit = pin_ads[pin_ads["pincode"].astype(str) == pin_match.group(0)]
            if not hit.empty:
                row = hit.iloc[0]
        return (
            f"**{row['area']} ({row['pincode']}) ad plan:** {row['ad_hook']}. "
            f"Channel: {row['channel']}. CTA: “{row['cta']}”."
        )

    if any(w in q for w in ["ad", "campaign", "meta", "google", "advertise"]):
        if pin_ads.empty:
            return "Generate advertising plans from the Ads tab first."
        top3 = pin_ads.head(3)
        lines = [f"- {r['area']}: {r['ad_hook']}" for _, r in top3.iterrows()]
        return "**Top ad opportunities**\n" + "\n".join(lines)

    if any(w in q for w in ["color", "size", "inventory", "stock", "warehouse"]):
        if taste_df.empty:
            return "Taste data unavailable."
        color = taste_df["color"].mode().iloc[0]
        size = taste_df["size"].mode().iloc[0]
        return (
            f"Dominant taste is **{color}** and size **{size}**. "
            f"Prioritize dark-store depth for these attributes in Champion-heavy hubs."
        )

    if "recommend" in q or "style" in q:
        if taste_df.empty:
            return "No wardrobe signals yet."
        row = taste_df.groupby(["brand", "commodity", "color"])["spend_share"].sum().sort_values(ascending=False)
        brand, commodity, color = row.index[0]
        return (
            f"**Style pick:** {color} {commodity} from {brand}. "
            f"This combo has the strongest spend affinity in the current cohort."
        )

    # default brief
    users = len(summary_df)
    rev = summary_df["total_spend"].sum() if not summary_df.empty else 0
    return (
        f"Cohort has **{users:,}** users and **₹{rev:,.0f}** spend. "
        f"Try asking: “top brand”, “ads for 560102”, “champion personas”, or “inventory colors”."
    )
