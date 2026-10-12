"""Personalized merchandise suggestions for the Customer Style Assistant."""

from __future__ import annotations

import pandas as pd

from services.recommendation_service import score_products_for_persona, similar_items


def customer_profile_row(summary_df: pd.DataFrame, customer_id: str) -> pd.Series | None:
    if summary_df.empty:
        return None
    hit = summary_df[summary_df["customer_id"] == str(customer_id)]
    if hit.empty:
        return None
    return hit.iloc[0]


def ensure_customer_profile(
    summary_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    auth_user: dict,
) -> pd.Series:
    """Return cohort profile, or a lightweight fallback for new / low-activity users."""
    cid = str(auth_user.get("customer_id") or "")
    row = customer_profile_row(summary_df, cid)
    if row is not None:
        return row

    u_orders = orders_df[orders_df["customer_id"] == cid] if not orders_df.empty else pd.DataFrame()
    spend = float(u_orders["order_amount"].sum()) if not u_orders.empty else 0.0
    n_orders = int(len(u_orders))
    return pd.Series({
        "customer_id": cid,
        "user_name": auth_user.get("user_name", "Guest"),
        "phone": auth_user.get("phone", ""),
        "gender": auth_user.get("gender", "Unknown"),
        "pincode": str(auth_user.get("pin_code") or "560102")[:6],
        "area": "Your area",
        "total_spend": spend,
        "total_orders": n_orders,
        "aov": (spend / n_orders) if n_orders else 0,
        "recency_days": 0,
        "rfm_score": 3,
        "persona": "Rising Shopper",
        "badge": "🌱 Rising Shopper",
        "lat": 12.93,
        "lon": 77.62,
    })


def wardrobe_for_user(taste_df: pd.DataFrame, customer_id: str) -> pd.DataFrame:
    if taste_df.empty:
        return pd.DataFrame()
    return taste_df[taste_df["customer_id"] == str(customer_id)].copy()


def suggest_merchandise(
    summary_df: pd.DataFrame,
    taste_df: pd.DataFrame,
    customer_id: str,
    top_n: int = 12,
) -> pd.DataFrame:
    """
    Build a personal merch feed:
    1) persona cohort winners
    2) lookalikes from the user's top brand/category
    """
    profile = customer_profile_row(summary_df, customer_id)
    persona = profile["persona"] if profile is not None else None
    mine = wardrobe_for_user(taste_df, customer_id)

    cohort = score_products_for_persona(taste_df, persona)
    rows = []

    if not cohort.empty:
        for _, r in cohort.head(top_n).iterrows():
            rows.append({
                "title": f"{r['color']} {r['commodity']}",
                "brand": r["brand"],
                "commodity": r["commodity"],
                "color": r["color"],
                "size": r.get("size", "M"),
                "relevance_score": r["relevance_score"],
                "reason": r["why"],
                "source": "persona_match",
            })

    if not mine.empty:
        top = mine.sort_values("spend_share", ascending=False).iloc[0]
        alts = similar_items(taste_df, top["brand"], top["commodity"], top_n=6)
        for _, r in alts.iterrows():
            rows.append({
                "title": f"{r['color']} {r['commodity']}",
                "brand": r["brand"],
                "commodity": r["commodity"],
                "color": r["color"],
                "size": top.get("size", "M"),
                "relevance_score": r["relevance_score"],
                "reason": f"Because you like {top['brand']} {top['commodity']} — {r['why']}",
                "source": "lookalike",
            })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).drop_duplicates(subset=["brand", "commodity", "color"])
    return out.sort_values("relevance_score", ascending=False).head(top_n)


def personal_assistant_reply(
    question: str,
    profile: pd.Series | None,
    merch_df: pd.DataFrame,
    wardrobe_df: pd.DataFrame,
) -> str:
    q = (question or "").strip().lower()
    name = profile["user_name"] if profile is not None else "there"
    persona = profile["persona"] if profile is not None else "your segment"
    area = profile["area"] if profile is not None else "your area"

    if not q:
        return f"Hi {name} — ask me what to wear, what to buy, or why an item was suggested."

    if any(w in q for w in ["suggest", "recommend", "merch", "buy", "wear", "outfit"]):
        if merch_df.empty:
            return "I don't have merch picks yet — place a few orders so we can learn your wardrobe."
        top = merch_df.iloc[0]
        extras = ", ".join(
            f"{r['brand']} {r['title']}" for _, r in merch_df.head(3).iterrows()
        )
        return (
            f"**{name}**, as a **{persona}** in **{area}**, start with "
            f"**{top['brand']} — {top['title']}** (score {top['relevance_score']}). "
            f"Also worth a look: {extras}."
        )

    if "why" in q or "justify" in q or "reason" in q:
        if merch_df.empty:
            return "No active suggestions to explain."
        top = merch_df.iloc[0]
        return f"**Why this pick:** {top['reason']} (source: {top['source']})."

    if any(w in q for w in ["wardrobe", "closet", "color", "size", "brand"]):
        if wardrobe_df.empty:
            return "Your captured wardrobe is still light. Upload garments on your next pickup for sharper picks."
        brand = wardrobe_df["brand"].mode().iloc[0]
        color = wardrobe_df["color"].mode().iloc[0]
        size = wardrobe_df["size"].mode().iloc[0]
        return (
            f"Your wardrobe signals lean **{brand}**, color **{color}**, size **{size}**. "
            f"I’ll keep pushing lookalikes in that lane."
        )

    if "persona" in q or "segment" in q or "rfm" in q:
        if profile is None:
            return "Profile not loaded."
        return (
            f"You're tagged **{profile['badge']}** ({persona}). "
            f"RFM score **{profile['rfm_score']}** · AOV ₹{profile['aov']:,.0f} · "
            f"{int(profile['total_orders'])} orders."
        )

    # default
    if not merch_df.empty:
        top = merch_df.iloc[0]
        return (
            f"Hi {name}. Top pick right now: **{top['brand']} {top['title']}**. "
            f"Ask “what should I buy?”, “why?”, or “my wardrobe”."
        )
    return f"Hi {name}. Ask about outfits, wardrobe colors, or your persona tips."
