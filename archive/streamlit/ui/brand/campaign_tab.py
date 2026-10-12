"""Tab 3 — Campaign creative upload + geo/demo/segment ranking."""

from __future__ import annotations

import streamlit as st
import plotly.express as px
import pandas as pd
from PIL import Image

from config import settings
from services.campaign_rank import analyze_creative, rank_campaign_by_segments, campaign_summary


def render_campaign_tab(
    my_brand: str,
    summary_df: pd.DataFrame,
    taste_df: pd.DataFrame,
) -> pd.DataFrame:
    """Returns the latest ranked dataframe for the chatbot context."""
    st.subheader("Campaign recommendation")
    st.caption("Upload a creative → score fit across geo × demographics × user segments")

    c1, c2 = st.columns([1, 1.2])
    with c1:
        uploaded = st.file_uploader("Campaign / product image", type=["jpg", "jpeg", "png"], key="campaign_img")
        commodity_hint = st.selectbox(
            "Intended commodity",
            ["Top Wear", "Bottom Wear", "Outerwear", "Ethnic Wear", "Home Linen", "Footwear"],
            key="campaign_commodity",
        )
        brand_hint = st.selectbox(
            "Brand on creative",
            [my_brand] + [b for b in settings.BRANDS if b != my_brand],
            key="campaign_brand",
        )

    ranked = st.session_state.get("campaign_ranked", pd.DataFrame())

    with c2:
        if uploaded is None:
            st.info("Upload an image to generate geo–demo rankings.")
        else:
            img = Image.open(uploaded)
            st.image(img, use_container_width=True, caption="Creative preview")
            creative = analyze_creative(img)
            st.write(
                f"**Detected:** color `{creative['detected_color']}` · "
                f"OCR brand `{creative['detected_brand'] or '—'}` · "
                f"size `{creative['detected_size'] or '—'}` · "
                f"lifecycle `{creative['lifecycle_score']}`"
            )
            if st.button("Rank campaign fit", type="primary", use_container_width=True):
                ranked = rank_campaign_by_segments(
                    taste_df, summary_df, creative,
                    commodity_hint=commodity_hint,
                    brand_hint=brand_hint,
                )
                st.session_state["campaign_ranked"] = ranked
                st.session_state["campaign_creative"] = creative
                st.success(campaign_summary(ranked, creative))

    ranked = st.session_state.get("campaign_ranked", ranked)
    creative = st.session_state.get("campaign_creative", {})

    if isinstance(ranked, pd.DataFrame) and not ranked.empty:
        st.markdown("#### Ranked flights (geo × segment)")
        fig = px.scatter(
            ranked.head(40),
            x="customers_in_zone",
            y="campaign_fit_score",
            color="persona",
            size="segment_share_pct",
            hover_name="area",
            hover_data=["why", "zone_top_color", "trending_commodity"],
            title="Where this creative will perform",
        )
        fig.update_layout(height=380, margin=dict(t=40, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

        top_areas = (
            ranked.groupby("area", as_index=False)["campaign_fit_score"].max()
            .sort_values("campaign_fit_score", ascending=False).head(10)
        )
        fig = px.bar(top_areas, x="area", y="campaign_fit_score", text_auto=True, title="Top zones for this creative")
        fig.update_layout(height=300, margin=dict(t=40, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(ranked, use_container_width=True, hide_index=True)
        if creative:
            st.info(campaign_summary(ranked, creative))
    return ranked if isinstance(ranked, pd.DataFrame) else pd.DataFrame()
