"""Customer intelligence: RFM, personas, PIN-code cohorts."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from config import settings


def clean_pincode(val: Any) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    match = re.search(r"\b560\d{3}\b", str(val))
    if match and match.group(0) in settings.TARGET_AREAS:
        return match.group(0)
    return None


def build_customer_summary(orders_df: pd.DataFrame, customers_df: pd.DataFrame) -> pd.DataFrame:
    if orders_df.empty:
        return pd.DataFrame()

    work = orders_df.dropna(subset=["customer_id"]).copy()
    if "order_date" in work.columns:
        work = work.dropna(subset=["order_date"])

    # Cohort: 3+ lifetime orders preferred
    counts = work.groupby("customer_id").size()
    qualified = set(counts[counts >= 3].index)
    if not qualified:
        qualified = set(work["customer_id"].unique())

    rows = []
    cust_map = {}
    if not customers_df.empty and "customer_id" in customers_df.columns:
        cust_map = customers_df.set_index("customer_id").to_dict("index")

    for cid in qualified:
        u = work[work["customer_id"] == cid]
        spend = float(u["order_amount"].sum())
        n_orders = int(len(u))
        last_date = u["order_date"].max() if "order_date" in u.columns else pd.NaT
        first_date = u["order_date"].min() if "order_date" in u.columns else pd.NaT

        meta = cust_map.get(cid, {})
        name = meta.get("customer_name") or f"User {str(cid)[:6]}"
        phone = meta.get("phone_no") or "N/A"
        gender = meta.get("gender") or "Unknown"
        pin = clean_pincode(meta.get("pin_code"))

        if not pin and "schedule" in u.columns:
            for sch in u["schedule"].dropna().head(3):
                if isinstance(sch, dict):
                    pin = clean_pincode(sch.get("address", {}).get("pin_code"))
                    if pin:
                        break

        # Never invent a PIN — Unknown stays Unknown (PRD geo contract).
        if not pin:
            pin = None

        # RFM-lite (demo UI); production path uses suffai.metrics.rfm
        max_date = work["order_date"].max() if "order_date" in work.columns else pd.Timestamp.utcnow()
        recency_days = int((max_date - last_date).days) if pd.notna(last_date) else 999
        r_score = 3 if recency_days <= 30 else (2 if recency_days <= 90 else 1)
        f_score = 3 if n_orders >= 8 else (2 if n_orders >= 4 else 1)
        m_score = 3 if spend >= 5000 else (2 if spend >= 2000 else 1)
        rfm = r_score + f_score + m_score

        if m_score == 3 and f_score == 3:
            persona = "Champion"
            badge = "⭐ Champion"
        elif m_score == 3:
            persona = "High Spender"
            badge = "💎 High Spender"
        elif f_score == 3:
            persona = "Loyal Frequent"
            badge = "🔄 High Frequency"
        else:
            persona = "Steady Regular"
            badge = "🛡️ Steady Regular"

        area = settings.TARGET_AREAS.get(pin or "", {}).get("area", "Unknown" if pin is None else pin)
        rows.append({
            "customer_id": str(cid),
            "user_name": str(name),
            "phone": str(phone),
            "gender": str(gender),
            "pincode": pin,
            "area": area,
            "total_spend": spend,
            "total_orders": n_orders,
            "aov": spend / n_orders if n_orders else 0,
            "recency_days": recency_days,
            "r_score": r_score,
            "f_score": f_score,
            "m_score": m_score,
            "rfm_score": rfm,
            "persona": persona,
            "badge": badge,
            "first_order": first_date,
            "last_order": last_date,
            "lat": settings.TARGET_AREAS.get(pin or "", {}).get("lat"),
            "lon": settings.TARGET_AREAS.get(pin or "", {}).get("lon"),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("total_spend", ascending=False)
    return df


def pincode_rollup(summary_df: pd.DataFrame) -> pd.DataFrame:
    if summary_df.empty:
        return pd.DataFrame()
    g = summary_df.groupby(["pincode", "area"], as_index=False).agg(
        users=("customer_id", "count"),
        revenue=("total_spend", "sum"),
        orders=("total_orders", "sum"),
        lat=("lat", "mean"),
        lon=("lon", "mean"),
        champions=("persona", lambda s: int((s == "Champion").sum())),
    )
    g["aov"] = (g["revenue"] / g["orders"].replace(0, np.nan)).fillna(0).round(2)
    return g.sort_values("revenue", ascending=False)
