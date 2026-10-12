"""Tab 4 — Brand chatbot over geo / competitor / research / campaign data."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from services.brand_assistant import answer_brand_query


def render_assistant_tab(
    my_brand: str,
    zone_df: pd.DataFrame,
    brand_bench: pd.DataFrame,
    gap_df: pd.DataFrame,
    product_rec: dict,
    campaign_ranked: pd.DataFrame,
    demo_df: pd.DataFrame,
) -> None:
    st.subheader("Brand intelligence assistant")
    st.caption("Answers use the same filtered geo, competitor, product-research, and campaign tables")

    if "brand_intel_chat" not in st.session_state:
        st.session_state.brand_intel_chat = [{
            "role": "assistant",
            "content": (
                f"Signed in as **{my_brand}**. Ask about zone density, competitor gaps, "
                f"new product briefs, or campaign rankings."
            ),
        }]

    for msg in st.session_state.brand_intel_chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.chat_input("Ask about zones, competitors, product research, or campaigns…")
    if prompt:
        st.session_state.brand_intel_chat.append({"role": "user", "content": prompt})
        reply = answer_brand_query(
            prompt,
            my_brand=my_brand,
            zone_df=zone_df,
            brand_bench=brand_bench,
            gap_df=gap_df,
            product_rec=product_rec,
            campaign_ranked=campaign_ranked,
            demo_df=demo_df,
        )
        st.session_state.brand_intel_chat.append({"role": "assistant", "content": reply})
        st.rerun()
