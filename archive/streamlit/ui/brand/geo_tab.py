"""Tab 1 — Geographic demand intelligence (GEO-01, governed)."""

from __future__ import annotations

import streamlit as st
import plotly.express as px
import pandas as pd

from services.governed import commodities_df, geo_dashboard, parse_area_selection, tiles_df


def render_geo_tab(
    selected_areas: list[str] | None = None,
    selected_segments: list[str] | None = None,
) -> dict:
    st.subheader("Geographic intelligence")
    st.caption(
        "Active customers by order PIN · service commodity popularity · RFM mix. "
        "Laundry quantities are service observations, not clothing purchases. "
        "Material/color/size/brand charts stay empty until consented garment photos exist."
    )

    geo_ids = parse_area_selection(selected_areas or []) if selected_areas else None
    result = geo_dashboard(geo_ids=geo_ids, rfm_segments=selected_segments)
    tiles = tiles_df(result)
    commodities = commodities_df(result)
    meta = result.get("meta") or {}

    mapped = tiles[tiles["geo_key"] != "UNKNOWN"] if not tiles.empty else tiles
    shown = mapped[~mapped["suppressed"]] if not mapped.empty and "suppressed" in mapped.columns else mapped

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Zones (unsuppressed)", len(shown) if shown is not None else 0)
    k2.metric("Mapped active customers", int(meta.get("mapped_active_customers") or 0))
    k3.metric("Unknown PIN customers", int(meta.get("unknown_pin_customers") or 0))
    k4.metric("Eligible orders", int(meta.get("eligible_orders") or 0))

    st.info(meta.get("coverage_note", ""))

    if tiles.empty:
        st.warning("No eligible orders for the current filters.")
        return result

    c1, c2 = st.columns([1.2, 1])
    with c1:
        st.markdown("#### Active customers by PIN")
        plot_df = shown.copy() if shown is not None else pd.DataFrame()
        if plot_df.empty:
            st.warning("All selected zones are privacy-suppressed (k=30) or unknown.")
        else:
            # Attach lat/lon for known Bengaluru demo hubs only
            plot_df["lat"] = plot_df["pin_code"].map(
                lambda p: {
                    "560102": 12.9121, "560103": 12.9279, "560034": 12.9352,
                    "560068": 12.9081, "560035": 12.9063, "560087": 12.9562,
                    "560037": 12.9698, "560066": 12.9698, "560048": 12.9925,
                    "560078": 12.9010, "560099": 12.8390, "560119": 12.8750,
                }.get(p)
            )
            plot_df["lon"] = plot_df["pin_code"].map(
                lambda p: {
                    "560102": 77.6445, "560103": 77.6841, "560034": 77.6245,
                    "560068": 77.6476, "560035": 77.6822, "560087": 77.7019,
                    "560037": 77.7123, "560066": 77.7500, "560048": 77.6844,
                    "560078": 77.5800, "560099": 77.6770, "560119": 77.6050,
                }.get(p)
            )
            mappable = plot_df.dropna(subset=["lat", "lon", "active_customers"])
            if mappable.empty:
                fig = px.bar(
                    plot_df.sort_values("active_customers", ascending=False).head(20),
                    x="area", y="active_customers", title="Active customers by zone",
                )
            else:
                map_fn = getattr(px, "scatter_map", None) or getattr(px, "scatter_mapbox", None)
                fig = map_fn(
                    mappable,
                    lat="lat",
                    lon="lon",
                    size="active_customers",
                    color="active_customers",
                    hover_name="area",
                    hover_data={"pin_code": True, "active_customers": True, "lat": False, "lon": False},
                    color_continuous_scale="YlOrRd",
                    size_max=42,
                    zoom=10.5,
                    height=420,
                    title="Active customers by order PIN (not census density)",
                    **(
                        {"map_style": "open-street-map"}
                        if map_fn.__name__ == "scatter_map"
                        else {"mapbox_style": "open-street-map"}
                    ),
                )
            fig.update_layout(margin=dict(l=10, r=10, t=40, b=10), height=420)
            st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.markdown("#### RFM mix (selected slice)")
        rfm_mix = pd.DataFrame(result.get("rfm_mix") or [])
        if rfm_mix.empty:
            st.caption("No RFM rows.")
        else:
            visible = rfm_mix[~rfm_mix["suppressed"]] if "suppressed" in rfm_mix.columns else rfm_mix
            if visible.empty:
                st.warning("RFM mix suppressed — cohort below privacy threshold.")
            else:
                fig = px.bar(
                    visible, x="rfm_segment", y="customers",
                    title="Eligible customers by RFM segment",
                )
                fig.update_layout(height=420, margin=dict(t=40, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Service commodity popularity")
    st.caption("Sum of ordered quantities on eligible orders — not verified garment purchases.")
    if commodities.empty:
        st.warning("No commodity lines for filters.")
    else:
        vis = commodities[~commodities["suppressed"]] if "suppressed" in commodities.columns else commodities
        if vis.empty:
            st.warning("Commodity breakdown suppressed (k-anonymity).")
        else:
            top = vis.head(15)
            fig = px.bar(
                top, x="quantity", y="commodity_name", orientation="h",
                title="Top commodities by service quantity",
            )
            fig.update_layout(height=420, margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                vis[["commodity_name", "quantity", "line_count", "distinct_customers", "note"]],
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("#### Zone table (includes suppressed flags)")
    display_cols = [c for c in ["area", "pin_code", "active_customers", "denominator", "suppressed"] if c in tiles.columns]
    st.dataframe(tiles[display_cols], use_container_width=True, hide_index=True)

    st.markdown("#### Attribute distributions (photo-verified)")
    st.warning(
        "No consented reviewed garment observations in `data_derived/`. "
        "Size / color / material / brand maps will populate after IMG-01/02."
    )

    return result
