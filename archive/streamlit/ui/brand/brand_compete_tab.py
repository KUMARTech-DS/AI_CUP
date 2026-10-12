"""Tab 2 — Brand & competitor + product research (verified garments only)."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from services.governed import brand_empty_state


def render_brand_compete_tab(my_brand: str, **_legacy) -> None:
    st.subheader(f"Brand & competitor intelligence · {my_brand}")
    st.caption(
        "Competitive share = share of verified consented garment observations in cohort. "
        "Laundry stores are not fashion brands. Order quantities are not ownership proof."
    )

    coverage = brand_empty_state()
    k1, k2, k3 = st.columns(3)
    k1.metric("Garment instances", coverage["garment_instances_total"])
    k2.metric("Verified branded", coverage["verified_branded_observations"])
    k3.metric("Analytics ready", "Yes" if coverage["brand_analytics_available"] else "No")

    if not coverage["brand_analytics_available"]:
        st.error(coverage["empty_state"])
        st.markdown(
            """
**To unlock this tab (Phase 2)**
1. Capture garment photos tied to order + commodity line (`IMG-01`)
2. Extract attributes with evidence (`IMG-02`)
3. Human-review uncertain brand fields
4. Onboard brand catalog SKUs for supply-side context

Until then, use **Geo intelligence** for service demand (commodity popularity, RFM, PIN density).
            """
        )
        st.markdown("#### Product research")
        st.warning(
            "Opportunity scoring needs verified category/attribute demand plus catalog coverage. "
            "Underserved-gap component is **unmeasured** without brand catalog / inventory."
        )
        return

    # When verified data exists, show simple share table
    from services.governed import garment_store

    verified = garment_store().list_verified_brand_observations()
    df = pd.DataFrame(verified)
    if "brand_name_raw" in df.columns:
        shares = (
            df.groupby("brand_name_raw", as_index=False)
            .size()
            .rename(columns={"size": "observed_garments"})
            .sort_values("observed_garments", ascending=False)
        )
        total = shares["observed_garments"].sum() or 1
        shares["share_of_verified_pct"] = (100.0 * shares["observed_garments"] / total).round(2)
        st.dataframe(shares, use_container_width=True, hide_index=True)
        st.caption(f"Highlighting tenant brand: **{my_brand}** (authorization enforced server-side in API).")
