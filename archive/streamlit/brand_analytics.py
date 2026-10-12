"""Brand vs competitor analytics with zone / demo / timeline filters."""

from __future__ import annotations

import pandas as pd


def filter_by_timeline(orders_df: pd.DataFrame, taste_df: pd.DataFrame, summary_df: pd.DataFrame, months: int | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Restrict customers/taste to those with orders in the last N months (approx)."""
    if not months or orders_df.empty or "order_date" not in orders_df.columns:
        return summary_df, taste_df

    max_dt = orders_df["order_date"].max()
    if pd.isna(max_dt):
        return summary_df, taste_df
    cutoff = max_dt - pd.DateOffset(months=months)
    recent_ids = set(orders_df.loc[orders_df["order_date"] >= cutoff, "customer_id"].dropna().astype(str))
    s = summary_df[summary_df["customer_id"].isin(recent_ids)].copy()
    t = taste_df[taste_df["customer_id"].isin(recent_ids)].copy() if not taste_df.empty else taste_df
    return s, t


def brand_zone_performance(taste_df: pd.DataFrame, my_brand: str) -> pd.DataFrame:
    if taste_df.empty:
        return pd.DataFrame()
    g = taste_df.groupby(["area", "brand"], as_index=False).agg(
        users=("customer_id", "nunique"),
        spend=("spend_share", "sum"),
        items=("commodity", "count"),
    )
    totals = g.groupby("area")["spend"].transform("sum").replace(0, 1)
    g["zone_share_pct"] = (g["spend"] / totals * 100).round(1)
    g["is_my_brand"] = g["brand"] == my_brand
    return g.sort_values(["area", "spend"], ascending=[True, False])


def brand_vs_competitors(taste_df: pd.DataFrame, my_brand: str) -> pd.DataFrame:
    if taste_df.empty:
        return pd.DataFrame()
    g = taste_df.groupby("brand", as_index=False).agg(
        users=("customer_id", "nunique"),
        spend=("spend_share", "sum"),
        top_commodity=("commodity", lambda s: s.mode().iloc[0] if not s.mode().empty else "—"),
        top_color=("color", lambda s: s.mode().iloc[0] if not s.mode().empty else "—"),
        top_size=("size", lambda s: s.mode().iloc[0] if not s.mode().empty else "—"),
    )
    total = g["spend"].sum() or 1
    g["share_of_wallet_pct"] = (g["spend"] / total * 100).round(1)
    g["is_my_brand"] = g["brand"] == my_brand
    g["rank"] = g["spend"].rank(ascending=False).astype(int)
    return g.sort_values("spend", ascending=False)


def attribute_split_for_brand(taste_df: pd.DataFrame, brand: str) -> dict[str, pd.DataFrame]:
    subset = taste_df[taste_df["brand"] == brand] if not taste_df.empty else taste_df
    if subset.empty:
        return {"commodity": pd.DataFrame(), "color": pd.DataFrame(), "size": pd.DataFrame(), "material": pd.DataFrame()}

    def counts(col):
        return (
            subset.groupby(col, as_index=False)
            .agg(users=("customer_id", "nunique"), spend=("spend_share", "sum"))
            .sort_values("spend", ascending=False)
        )

    out = {
        "commodity": counts("commodity"),
        "color": counts("color"),
        "size": counts("size"),
    }
    if "material" in subset.columns:
        out["material"] = counts("material")
    else:
        out["material"] = pd.DataFrame()
    return out


def competitor_gap(taste_df: pd.DataFrame, my_brand: str) -> pd.DataFrame:
    """Where competitors win zones / attributes vs my brand."""
    if taste_df.empty:
        return pd.DataFrame()
    zone = brand_zone_performance(taste_df, my_brand)
    mine = zone[zone["is_my_brand"]][["area", "zone_share_pct", "spend", "users"]].rename(
        columns={"zone_share_pct": "my_share_pct", "spend": "my_spend", "users": "my_users"}
    )
    leaders = (
        zone.sort_values("spend", ascending=False)
        .groupby("area", as_index=False)
        .first()[["area", "brand", "zone_share_pct", "spend"]]
        .rename(columns={"brand": "zone_leader", "zone_share_pct": "leader_share_pct", "spend": "leader_spend"})
    )
    gap = leaders.merge(mine, on="area", how="left").fillna({"my_share_pct": 0, "my_spend": 0, "my_users": 0})
    gap["share_gap_pct"] = (gap["leader_share_pct"] - gap["my_share_pct"]).round(1)
    gap["opportunity"] = gap.apply(
        lambda r: "Defend leadership" if r["zone_leader"] == my_brand else f"Challenge {r['zone_leader']}",
        axis=1,
    )
    return gap.sort_values("share_gap_pct", ascending=False)
