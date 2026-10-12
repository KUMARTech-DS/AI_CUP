"""Advertising intelligence: PIN-level (3P) and user-level (1P Suffai) recommendations."""

from __future__ import annotations

import pandas as pd


def pin_level_ad_plans(taste_df: pd.DataFrame, pincode_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregated creative / inventory tips per PIN for third-party ad platforms."""
    if taste_df.empty:
        return pd.DataFrame()

    zone = (
        taste_df.groupby(["pincode", "area", "brand", "commodity", "color"], as_index=False)
        .agg(users=("customer_id", "nunique"), spend=("spend_share", "sum"))
    )
    # top combo per pincode
    zone = zone.sort_values(["pincode", "spend"], ascending=[True, False])
    top = zone.groupby("pincode", as_index=False).head(1)

    if not pincode_df.empty:
        top = top.merge(
            pincode_df[["pincode", "revenue", "users", "aov"]],
            on="pincode",
            how="left",
            suffixes=("", "_cohort"),
        )

    top["channel"] = "Meta / Google / Programmatic (3P)"
    top["ad_hook"] = top.apply(
        lambda r: f"Push {r['color']} {r['commodity']} from {r['brand']} in {r['area']}",
        axis=1,
    )
    top["cta"] = "Shop lookalike styles near you"
    top["priority"] = top["spend"].rank(ascending=False).astype(int)
    return top.sort_values("spend", ascending=False)


def user_level_ad_plans(summary_df: pd.DataFrame, taste_df: pd.DataFrame, limit: int = 40) -> pd.DataFrame:
    """First-party Suffai offers personalized per user."""
    if summary_df.empty or taste_df.empty:
        return pd.DataFrame()

    top_taste = (
        taste_df.sort_values("spend_share", ascending=False)
        .groupby("customer_id", as_index=False)
        .head(1)
    )
    merged = summary_df.merge(
        top_taste[["customer_id", "brand", "commodity", "color", "size"]],
        on="customer_id",
        how="left",
    )
    merged["channel"] = "Suffai App / WhatsApp (1P)"
    merged["offer"] = merged.apply(
        lambda r: f"15% on {r.get('color', 'your')} {r.get('commodity', 'essentials')} · {r.get('brand', 'preferred brand')}",
        axis=1,
    )
    merged["why"] = merged.apply(
        lambda r: f"{r['persona']} · RFM {r['rfm_score']} · AOV ₹{r['aov']:,.0f}",
        axis=1,
    )
    return merged.sort_values("total_spend", ascending=False).head(limit)[
        ["user_name", "phone", "pincode", "area", "persona", "brand", "commodity", "color", "offer", "channel", "why", "total_spend"]
    ]
