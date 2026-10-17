"""
SUFFAI Brand Intelligence Portal (PRD-aligned)

Nav: Geo Intelligence | Brand & Competition | Campaign Lab
(Chat lives on Command Center — see command_center/chatbot.py)

Run: streamlit run brand_app.py --server.port 8503
"""

from __future__ import annotations

import streamlit as st
import pandas as pd

from ui.brand_styles import inject_brand
from ui.brand import (
    render_geo_tab,
    render_brand_compete_tab,
    render_campaign_tab,
    render_assistant_tab,
)
from services.data_loader import runtime_mode
from services.auth_service import list_brand_accounts, verify_brand_login, BRAND_DEMO_PASSWORD
from services.governed import load_governed, rfm_summary_df, brand_empty_state
from suffai.metrics.geo import PIN_AREA_LABELS

st.set_page_config(
    page_title="SUFFAI · Brand Intelligence",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_brand(st)

if "brand_user" not in st.session_state:
    st.session_state.brand_user = None


def brand_logout():
    for key in ["brand_user", "brand_intel_chat", "campaign_ranked", "campaign_creative"]:
        st.session_state.pop(key, None)
    st.rerun()


# ========================= LOGIN =========================
if st.session_state.brand_user is None:
    st.markdown('<div class="brand-title">SUFFAI Brand Intelligence</div>', unsafe_allow_html=True)
    st.markdown(
        '<p class="brand-sub">Geo · competitor · product research · campaign ranking · assistant</p>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="login-shell">', unsafe_allow_html=True)
    with st.form("brand_login_form", clear_on_submit=False):
        brand = st.selectbox("Brand account", list_brand_accounts())
        password = st.text_input(
            "Password",
            type="password",
            value="",
            help=f"Demo password is: {BRAND_DEMO_PASSWORD}",
        )
        submitted = st.form_submit_button("Enter brand workspace", type="primary", use_container_width=True)
        if submitted:
            user = verify_brand_login(brand, password)
            if user is None:
                st.error(
                    f"Invalid credentials. Type exactly: **{BRAND_DEMO_PASSWORD}** "
                    f"(also accepts `demo` or `suffai`)."
                )
            else:
                st.session_state.brand_user = user
                st.rerun()

    if st.button("⚡ Quick demo login (Peter England)", use_container_width=True):
        st.session_state.brand_user = verify_brand_login("Peter England", BRAND_DEMO_PASSWORD)
        st.rerun()

    st.caption(f"Password: `{BRAND_DEMO_PASSWORD}` · or click Quick demo login")
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

brand_user = st.session_state.brand_user
my_brand = brand_user["brand"]

# ========================= GOVERNED DATA =========================
gov = load_governed()
integrity = gov["integrity"]
coverage = brand_empty_state()

st.sidebar.markdown(f"### 🏷️ {my_brand}")
st.sidebar.caption(f"{brand_user['email']} · {runtime_mode()} · governed metrics")
if st.sidebar.button("Log out"):
    brand_logout()

st.sidebar.markdown("### Filters")
pins = sorted({o.get("order_pin") for o in gov["orders"] if o.get("order_pin")})
area_labels = [f"{PIN_AREA_LABELS.get(p, p)} ({p})" for p in pins]
f_areas = st.sidebar.multiselect("Zones / PINs", area_labels, default=[])
rfm_df = rfm_summary_df()
all_segments = rfm_df["rfm_segment"].tolist() if not rfm_df.empty else []
f_segments = st.sidebar.multiselect("RFM segments", all_segments, default=[])

st.sidebar.markdown("### Data freshness")
st.sidebar.write(f"Orders: {integrity['counts']['orders']:,}")
st.sidebar.write(f"Commodity lines: {integrity['counts']['order_commodity_lines']:,}")
st.sidebar.write(f"Join OK: {integrity['gates']['correct_customer_join_majority']}")
st.sidebar.caption("Source: data_offline (read-only). Derived: data_derived/")

# ========================= HEADER =========================
st.markdown(f'<div class="brand-title">{my_brand} · Intelligence Desk</div>', unsafe_allow_html=True)
st.markdown(
    '<p class="brand-sub">Privacy-preserving fashion demand intelligence from laundry service '
    "observations + consented garment images. Not market sales.</p>",
    unsafe_allow_html=True,
)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Eligible order lines", f"{integrity['counts']['order_commodity_lines']:,}")
k2.metric("RFM customers", f"{len(gov['rfm']):,}")
k3.metric("Verified garments", coverage["verified_branded_observations"])
k4.metric("Brand analytics", "Ready" if coverage["brand_analytics_available"] else "Waiting on photos")

tab_geo, tab_brand, tab_campaign, tab_chat = st.tabs([
    "🗺️ Geo Intelligence",
    "🏷️ Brand & Competition",
    "📣 Campaign Lab",
    "💬 Ask Suffai",
])

with tab_geo:
    render_geo_tab(
        selected_areas=f_areas or None,
        selected_segments=f_segments or None,
    )

with tab_brand:
    render_brand_compete_tab(my_brand=my_brand)

with tab_campaign:
    st.caption(
        "Campaign fit uses photo-derived attributes + geo/RFM demand. "
        "Without verified garment cohorts, rankings emphasize geo × RFM coverage only."
    )
    # Legacy campaign UI expects summary/taste frames — pass empty taste (no invented brands)
    empty_summary = pd.DataFrame(columns=["customer_id", "area", "persona", "gender", "total_spend", "pincode"])
    empty_taste = pd.DataFrame(columns=["customer_id", "brand", "color", "size", "commodity", "area", "persona"])
    render_campaign_tab(my_brand, empty_summary, empty_taste)

with tab_chat:
    st.subheader("Chat moved to Command Center")
    st.info(
        "Dashboard chat (metrics.json + Geo/Ads filters → Gemini) is in "
        "`command_center/chatbot.py`, exposed as POST /v1/chat on the Command Center shell."
    )
    with st.expander("Legacy prototype assistant (deprecated)"):
        render_assistant_tab(
            my_brand=my_brand,
            zone_df=pd.DataFrame(),
            brand_bench=pd.DataFrame(),
            gap_df=pd.DataFrame(),
            product_rec={"ok": False},
            campaign_ranked=pd.DataFrame(),
            demo_df=pd.DataFrame(),
        )
