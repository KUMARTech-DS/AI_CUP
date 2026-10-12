"""Geographic / zone intelligence from customer + wardrobe taste data."""

from __future__ import annotations

import random

import pandas as pd

from config import settings

MATERIALS = ["Cotton", "Linen", "Wool Blend", "Polyester", "Denim", "Silk"]


def enrich_taste_with_material(taste_df: pd.DataFrame) -> pd.DataFrame:
    """Attach a deterministic material type (proxy until fabric CV is live)."""
    if taste_df.empty:
        return taste_df
    df = taste_df.copy()
    if "material" not in df.columns:
        df["material"] = df.apply(
            lambda r: MATERIALS[hash(str(r.get("brand", "")) + str(r.get("commodity", ""))) % len(MATERIALS)],
            axis=1,
        )
    return df


def apply_demo_filters(
    summary_df: pd.DataFrame,
    taste_df: pd.DataFrame,
    areas: list[str] | None = None,
    personas: list[str] | None = None,
    genders: list[str] | None = None,
    min_spend: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    s = summary_df.copy()
    if s.empty:
        return s, taste_df.iloc[0:0]

    if areas:
        s = s[s["area"].isin(areas)]
    if personas:
        s = s[s["persona"].isin(personas)]
    if genders:
        s = s[s["gender"].astype(str).isin(genders)]
    if min_spend:
        s = s[s["total_spend"] >= min_spend]

    ids = set(s["customer_id"])
    t = taste_df[taste_df["customer_id"].isin(ids)].copy() if not taste_df.empty else taste_df
    return s, t


def zone_density_table(summary_df: pd.DataFrame, taste_df: pd.DataFrame) -> pd.DataFrame:
    """One row per zone with density + trending attributes."""
    if summary_df.empty:
        return pd.DataFrame()

    taste_df = enrich_taste_with_material(taste_df)
    rows = []
    for area, grp in summary_df.groupby("area"):
        pin = grp["pincode"].mode().iloc[0] if not grp["pincode"].empty else ""
        lat = float(grp["lat"].mean()) if "lat" in grp else settings.TARGET_AREAS.get(str(pin), {}).get("lat", 12.93)
        lon = float(grp["lon"].mean()) if "lon" in grp else settings.TARGET_AREAS.get(str(pin), {}).get("lon", 77.62)
        seg = (grp["persona"].value_counts(normalize=True) * 100).round(1).to_dict()
        zt = taste_df[taste_df["area"] == area] if not taste_df.empty and "area" in taste_df.columns else pd.DataFrame()

        def mode_or(series, default="—"):
            if series is None or len(series) == 0:
                return default
            m = series.mode()
            return m.iloc[0] if not m.empty else default

        rows.append({
            "area": area,
            "pincode": pin,
            "lat": lat,
            "lon": lon,
            "customers": int(len(grp)),
            "density_index": int(len(grp)),
            "revenue": float(grp["total_spend"].sum()),
            "aov": float(grp["aov"].mean()) if "aov" in grp else 0,
            "trending_commodity": mode_or(zt["commodity"] if not zt.empty else None),
            "top_size": mode_or(zt["size"] if not zt.empty else None),
            "top_color": mode_or(zt["color"] if not zt.empty else None),
            "top_material": mode_or(zt["material"] if not zt.empty else None),
            "top_brand": mode_or(zt["brand"] if not zt.empty else None),
            "segment_mix": ", ".join(f"{k} {v}%" for k, v in list(seg.items())[:4]),
            "champion_pct": float(seg.get("Champion", 0)),
            "high_spender_pct": float(seg.get("High Spender", 0)),
            "loyal_pct": float(seg.get("Loyal Frequent", 0)),
            "steady_pct": float(seg.get("Steady Regular", 0)),
            "female_pct": float((grp["gender"].astype(str).str.lower().eq("female").mean() * 100) if "gender" in grp else 0),
            "male_pct": float((grp["gender"].astype(str).str.lower().eq("male").mean() * 100) if "gender" in grp else 0),
        })
    return pd.DataFrame(rows).sort_values("customers", ascending=False)


def demographic_breakdown(summary_df: pd.DataFrame) -> pd.DataFrame:
    if summary_df.empty:
        return pd.DataFrame()
    g = summary_df.groupby(["area", "persona", "gender"], as_index=False).agg(
        customers=("customer_id", "count"),
        spend=("total_spend", "sum"),
    )
    total = g.groupby("area")["customers"].transform("sum").replace(0, 1)
    g["pct_of_zone"] = (g["customers"] / total * 100).round(1)
    return g.sort_values(["area", "customers"], ascending=[True, False])
