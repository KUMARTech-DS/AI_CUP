"""
SUFFAI Fashion Intelligence — portal chooser.

Customer UI:  streamlit run customer_app.py --server.port 8502
Brand UI:     streamlit run brand_app.py --server.port 8503

This file is a lightweight landing page (port 8501).
"""

from __future__ import annotations

import streamlit as st

from config import settings
from ui.styles import inject

st.set_page_config(page_title=settings.APP_NAME, page_icon="👔", layout="centered")
inject(st)

st.markdown(f'<div class="hero-brand">{settings.APP_NAME}</div>', unsafe_allow_html=True)
st.markdown(
    '<p class="hero-sub">Choose your workspace. Customer and Brand portals are separate apps with their own login.</p>',
    unsafe_allow_html=True,
)

c1, c2 = st.columns(2)
with c1:
    st.markdown("### ✨ Customer Style")
    st.write("Login with phone · Style Assistant · suggested merchandise")
    st.code("streamlit run customer_app.py --server.port 8502", language="bash")
    st.caption("Demo: +919686862320 · OTP 2320")
with c2:
    st.markdown("### 📊 Brand Intelligence")
    st.write("Brand login · ads, RFM, catalog, capture, inventory")
    st.code("streamlit run brand_app.py --server.port 8503", language="bash")
    st.caption("Demo password for all brands: brand123")

st.markdown("---")
st.markdown(
    """
**Modules behind both UIs**
- `services/auth_service.py` — customer OTP + brand password
- `services/merch_service.py` — personal merch feed
- Customer / brand analytics services under `services/`
"""
)
