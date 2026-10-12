"""Catalog / SKU management over commodities + captured garment metadata."""

from __future__ import annotations

import pandas as pd

from services.product_intel import load_captured_catalog


def build_sku_catalog(commodities_df: pd.DataFrame, storeservices_df: pd.DataFrame) -> pd.DataFrame:
    if commodities_df.empty:
        return pd.DataFrame()

    cat = commodities_df.copy()
    if "_id" in cat.columns:
        cat["commodity_id"] = cat["_id"].apply(
            lambda v: v.get("$oid") if isinstance(v, dict) else str(v)
        )
    cat["sku_name"] = cat.get("name", pd.Series(["Unknown"] * len(cat))).astype(str)
    cat["category"] = cat.get("menu", pd.Series(["General"] * len(cat))).astype(str)
    cat["service_name"] = cat.get("service_name", pd.Series([""] * len(cat))).astype(str)

    if not storeservices_df.empty and "commodity_id" in storeservices_df.columns:
        ss = storeservices_df.copy()
        if "commodity_id" in ss.columns:
            ss["commodity_id"] = ss["commodity_id"].apply(
                lambda v: v.get("$oid") if isinstance(v, dict) else str(v)
            )
        price_map = ss.groupby("commodity_id")["price"].mean() if "price" in ss.columns else pd.Series(dtype=float)
        cat["avg_price"] = cat["commodity_id"].map(price_map)
    else:
        cat["avg_price"] = None

    cols = [c for c in ["commodity_id", "sku_name", "category", "service_name", "avg_price", "image_url", "weight"] if c in cat.columns]
    out = cat[cols]
    if "commodity_id" in out.columns:
        out = out.drop_duplicates(subset=["commodity_id"])
    return out


def merged_product_catalog(commodities_df: pd.DataFrame, storeservices_df: pd.DataFrame) -> pd.DataFrame:
    base = build_sku_catalog(commodities_df, storeservices_df)
    captured = load_captured_catalog()
    if captured.empty:
        return base
    # Keep both views available via a source tag
    base = base.copy()
    base["source"] = "master_sku"
    captured = captured.copy()
    captured["source"] = "edge_capture"
    return base
