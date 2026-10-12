"""Competitive benchmarking and recommendation-driven inventory signals."""

from __future__ import annotations

import pandas as pd


def brand_benchmark(taste_df: pd.DataFrame) -> pd.DataFrame:
    if taste_df.empty:
        return pd.DataFrame()
    g = taste_df.groupby("brand", as_index=False).agg(
        users=("customer_id", "nunique"),
        wardrobe_items=("commodity", "count"),
        spend=("spend_share", "sum"),
    )
    total = g["spend"].sum() or 1
    g["share_of_wallet_pct"] = (g["spend"] / total * 100).round(1)
    g = g.sort_values("spend", ascending=False)
    g["rank"] = range(1, len(g) + 1)
    return g


def inventory_signals(taste_df: pd.DataFrame) -> pd.DataFrame:
    """Dark-store / warehouse sizing demand from wardrobe attributes."""
    if taste_df.empty:
        return pd.DataFrame()
    g = (
        taste_df.groupby(["area", "commodity", "size", "color"], as_index=False)
        .agg(demand_users=("customer_id", "nunique"), spend=("spend_share", "sum"))
        .sort_values("demand_users", ascending=False)
    )
    g["stock_action"] = g["demand_users"].apply(
        lambda u: "Increase safety stock" if u >= 8 else ("Monitor" if u >= 3 else "Lean")
    )
    return g.head(100)
