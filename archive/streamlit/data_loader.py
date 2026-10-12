"""Load offline JSON (local) with optional GCS / Mongo hooks for GCP."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from config import settings


def extract_id(val: Any) -> str | None:
    if isinstance(val, dict) and "$oid" in val:
        return str(val["$oid"])
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if pd.isna(val):
        return None
    return str(val).strip()


def extract_date(val: Any) -> pd.Timestamp:
    if isinstance(val, dict) and "$date" in val:
        d_val = val["$date"]
        if isinstance(d_val, dict) and "$numberLong" in d_val:
            return pd.to_datetime(int(d_val["$numberLong"]), unit="ms", errors="coerce", utc=True)
        return pd.to_datetime(d_val, errors="coerce", utc=True)
    return pd.to_datetime(val, errors="coerce", utc=True)


def _read_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


def _maybe_download_from_gcs(local_dir: Path) -> None:
    """If GCS_BUCKET is set, sync JSON files into DATA_DIR (best-effort)."""
    bucket = settings.GCS_BUCKET
    if not bucket:
        return
    try:
        from google.cloud import storage  # type: ignore

        client = storage.Client()
        b = client.bucket(bucket)
        local_dir.mkdir(parents=True, exist_ok=True)
        for blob in b.list_blobs(prefix="data_offline/"):
            if not blob.name.endswith(".json"):
                continue
            dest = local_dir / Path(blob.name).name
            if not dest.exists():
                blob.download_to_filename(str(dest))
    except Exception as exc:  # pragma: no cover
        st.warning(f"GCS sync skipped: {exc}")


@st.cache_data(ttl=300, show_spinner="Loading fashion intelligence data...")
def load_all_datasets() -> dict[str, pd.DataFrame]:
    data_dir = Path(settings.DATA_DIR)
    _maybe_download_from_gcs(data_dir)

    files = {
        "orders": "orders.json",
        "customers": "customers.json",
        "commodities": "commodities.json",
        "stores": "stores.json",
        "services": "services.json",
        "storeservices": "storeservices.json",
        "coupons": "usercouponusages.json",
    }

    frames: dict[str, pd.DataFrame] = {}
    for key, filename in files.items():
        raw = _read_json(data_dir / filename)
        frames[key] = pd.DataFrame(raw) if raw else pd.DataFrame()

    orders = frames["orders"]
    customers = frames["customers"]

    if not orders.empty:
        if "_id" in orders.columns:
            orders["_id"] = orders["_id"].apply(extract_id)
        if "customer_id" in orders.columns:
            orders["customer_id"] = orders["customer_id"].apply(extract_id)
        if "store_id" in orders.columns:
            orders["store_id"] = orders["store_id"].apply(extract_id)

        date_src = "scheduled_date" if "scheduled_date" in orders.columns else "created_at"
        if "order_date_time" in orders.columns:
            date_src = "order_date_time"
        orders["order_date"] = orders[date_src].apply(extract_date) if date_src in orders.columns else pd.NaT

        def amount(row):
            amt = row.get("total_amount", 0)
            if pd.isna(amt) or amt == 0:
                base = row.get("actual_price_without_discount", 0) or 0
                amt = (base or 0) + (row.get("pickup_fee", 0) or 0) + (row.get("delivery_fee", 0) or 0)
            return float(amt or 0)

        orders["order_amount"] = orders.apply(amount, axis=1)
        orders["bill_number"] = orders.get("bill_number", pd.Series([""] * len(orders))).fillna("").astype(str).str.strip()
        orders["order_status"] = orders.get("order_status", pd.Series([""] * len(orders))).fillna("").astype(str)

    if not customers.empty:
        if "_id" in customers.columns:
            customers["customer_id"] = customers["_id"].apply(extract_id)
        customers["pin_code"] = customers.get("pin_code", pd.Series([None] * len(customers)))
        customers["pin_code"] = customers["pin_code"].astype(str).str.extract(r"(560\d{3})", expand=False)

    frames["orders"] = orders
    frames["customers"] = customers
    return frames


def runtime_mode() -> str:
    if settings.GCS_BUCKET:
        return "GCP / GCS"
    if settings.MONGO_URI:
        return "MongoDB"
    return "Offline local"
