"""AI recommendation engine: metadata similarity + persona-aware rankings."""

from __future__ import annotations

import pandas as pd


def score_products_for_persona(taste_df: pd.DataFrame, persona: str | None = None) -> pd.DataFrame:
    """Rank brand/commodity/color combos by spend affinity for a persona."""
    if taste_df.empty:
        return pd.DataFrame()

    df = taste_df if not persona or persona == "All" else taste_df[taste_df["persona"] == persona]
    if df.empty:
        df = taste_df

    ranked = (
        df.groupby(["brand", "commodity", "color", "size"], as_index=False)
        .agg(users=("customer_id", "nunique"), affinity_spend=("spend_share", "sum"))
        .sort_values("affinity_spend", ascending=False)
    )
    if ranked.empty:
        return ranked

    max_spend = ranked["affinity_spend"].max() or 1
    ranked["relevance_score"] = (ranked["affinity_spend"] / max_spend * 100).round(1)
    ranked["why"] = ranked.apply(
        lambda r: f"High overlap with {persona or 'cohort'} wardrobes · {r['users']} users · {r['color']} {r['commodity']}",
        axis=1,
    )
    return ranked.head(50)


def similar_items(taste_df: pd.DataFrame, brand: str, commodity: str, top_n: int = 8) -> pd.DataFrame:
    """Simple metadata similarity: same commodity / overlapping colors in other brands."""
    if taste_df.empty:
        return pd.DataFrame()

    seed = taste_df[(taste_df["brand"] == brand) & (taste_df["commodity"] == commodity)]
    colors = set(seed["color"].unique()) if not seed.empty else set()
    pool = taste_df[taste_df["commodity"] == commodity].copy()
    if pool.empty:
        pool = taste_df.copy()

    pool["sim"] = pool["color"].isin(colors).astype(int) * 2 + (pool["brand"] != brand).astype(int)
    ranked = (
        pool.groupby(["brand", "commodity", "color"], as_index=False)
        .agg(sim=("sim", "mean"), users=("customer_id", "nunique"), spend=("spend_share", "sum"))
        .sort_values(["sim", "spend"], ascending=False)
    )
    ranked = ranked[ranked["brand"] != brand].head(top_n)
    ranked["relevance_score"] = (50 + ranked["sim"] * 20).clip(0, 99).round(1)
    ranked["why"] = "Shared category + color taste overlap across competitor wardrobes"
    return ranked
