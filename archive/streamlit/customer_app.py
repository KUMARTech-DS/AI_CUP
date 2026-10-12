"""
SUFFAI Customer Portal
Login → Style profile → Suggested merchandise → Personal Style Assistant

Run:  streamlit run customer_app.py --server.port 8502
"""

from __future__ import annotations

import streamlit as st
import plotly.express as px

from config import settings
from ui.customer_styles import inject_customer
from services.data_loader import load_all_datasets
from services.customer_intel import build_customer_summary
from services.product_intel import build_wardrobe_taste
from services.auth_service import customer_lookup, verify_customer_otp, normalize_phone
from services.merch_service import (
    ensure_customer_profile,
    wardrobe_for_user,
    suggest_merchandise,
    personal_assistant_reply,
)

st.set_page_config(
    page_title="SUFFAI · My Style",
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_customer(st)

# ---- session ----
if "customer_user" not in st.session_state:
    st.session_state.customer_user = None
if "cust_chat" not in st.session_state:
    st.session_state.cust_chat = []


def logout():
    st.session_state.customer_user = None
    st.session_state.cust_chat = []
    st.rerun()


datasets = load_all_datasets()
customers_df = datasets["customers"]
orders_df = datasets["orders"]
summary_df = build_customer_summary(orders_df, customers_df)
taste_df = build_wardrobe_taste(summary_df)

# ========================= LOGIN =========================
if st.session_state.customer_user is None:
    st.markdown('<div class="cust-brand">SUFFAI Style</div>', unsafe_allow_html=True)
    st.markdown(
        '<p class="cust-sub">Customer login — personal Style Assistant & merch picks made for you.</p>',
        unsafe_allow_html=True,
    )

    with st.container():
        st.markdown('<div class="login-shell">', unsafe_allow_html=True)
        st.markdown("#### Sign in with your mobile number")
        phone = st.text_input("Phone", placeholder="+91 96868 62320", key="login_phone")
        otp = st.text_input(
            "OTP (demo = last 4 digits of phone)",
            type="password",
            placeholder="2320",
            key="login_otp",
        )
        if st.button("Continue to my style", type="primary", use_container_width=True):
            user = customer_lookup(customers_df, phone)
            if user is None:
                st.error("No customer found for that phone. Use a number from the Suffai customer list.")
            elif not verify_customer_otp(phone, otp):
                st.error(f"Invalid OTP. Demo tip: use last 4 digits → **{normalize_phone(phone)[-4:]}**")
            else:
                st.session_state.customer_user = user
                st.session_state.cust_chat = [{
                    "role": "assistant",
                    "content": f"Welcome back, **{user['user_name']}**! Ask me what to buy or why a pick was suggested.",
                }]
                st.rerun()

        st.caption("Demo: phone `+919686862320` · OTP `2320` (Yugu · Champion)")
        st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# ========================= AUTHENTICATED =========================
user = st.session_state.customer_user
profile = ensure_customer_profile(summary_df, orders_df, user)
wardrobe = wardrobe_for_user(taste_df, profile["customer_id"])
merch = suggest_merchandise(summary_df, taste_df, profile["customer_id"])

top = st.columns([4, 1])
with top[0]:
    st.markdown('<div class="cust-brand">Your Style Assistant</div>', unsafe_allow_html=True)
    st.markdown(
        f'<p class="cust-sub">Hi {profile["user_name"]} · {profile["badge"]} · '
        f'{profile.get("area", "Bengaluru")}</p>',
        unsafe_allow_html=True,
    )
with top[1]:
    if st.button("Log out"):
        logout()

tab_merch, tab_assistant, tab_profile = st.tabs([
    "🛍️ Suggested for you",
    "💬 Style Assistant",
    "👤 My profile",
])

# ----- Merch -----
with tab_merch:
    st.subheader("Merchandise matched to your persona & wardrobe")
    if merch.empty:
        st.info(
            "No photo-verified wardrobe attributes yet. "
            "Suggestions unlock after consented garment capture — we don't invent brands from laundry quantity."
        )
    else:
        for i in range(0, len(merch), 3):
            cols = st.columns(3)
            chunk = merch.iloc[i:i + 3]
            for col, (_, item) in zip(cols, chunk.iterrows()):
                with col:
                    st.markdown(
                        f"""
                        <div class="merch-card">
                          <span class="score-pill">{item['relevance_score']:.0f} match</span>
                          <h4>{item['brand']}</h4>
                          <div style="font-weight:700;margin:0.2rem 0;">{item['title']}</div>
                          <div style="color:#57534E;font-size:0.9rem;">Size cue: {item['size']} · {item['source']}</div>
                          <p style="color:#44403C;font-size:0.88rem;margin-top:0.55rem;">{item['reason']}</p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if st.button("Save look", key=f"save_{i}_{item['brand']}_{item['color']}"):
                        st.success(f"Saved {item['brand']} {item['title']} to your lookbook.")

        st.markdown("#### How these picks score")
        fig = px.bar(
            merch.head(10), x="relevance_score", y="title", color="brand",
            orientation="h", hover_data=["reason"], title="Relevance ranking",
        )
        fig.update_layout(height=360, margin=dict(t=40, b=10, l=10, r=10), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

# ----- Assistant -----
with tab_assistant:
    st.subheader("Chat with your Style Assistant")
    st.caption("Ask: what should I buy? · why this pick? · my wardrobe · my persona")

    for msg in st.session_state.cust_chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.chat_input("Ask about outfits, merch, or your style profile…")
    if prompt:
        st.session_state.cust_chat.append({"role": "user", "content": prompt})
        reply = personal_assistant_reply(prompt, profile, merch, wardrobe)
        st.session_state.cust_chat.append({"role": "assistant", "content": reply})
        st.rerun()

# ----- Profile -----
with tab_profile:
    st.subheader("Style profile")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Persona", profile["persona"])
    c2.metric("Lifetime spend", f"₹{float(profile['total_spend']):,.0f}")
    c3.metric("Orders", int(profile["total_orders"]))
    c4.metric("RFM", profile.get("rfm_score", "—"))

    st.markdown(
        f"**Phone:** {profile.get('phone', user['phone'])}  \n"
        f"**Gender:** {profile.get('gender', '—')}  \n"
        f"**Hub:** {profile.get('area', '—')} ({profile.get('pincode', '—')})"
    )

    if wardrobe.empty:
        st.info("No wardrobe attribute signals yet — suggestions use your persona cohort.")
    else:
        st.markdown("#### Wardrobe signals")
        w1, w2 = st.columns(2)
        with w1:
            fig = px.pie(wardrobe, names="brand", title="Brand mix")
            fig.update_layout(height=300, margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(fig, use_container_width=True)
        with w2:
            fig = px.pie(wardrobe, names="color", title="Color mix")
            fig.update_layout(height=300, margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(
            wardrobe[["brand", "commodity", "color", "size", "care"]],
            use_container_width=True, hide_index=True,
        )
