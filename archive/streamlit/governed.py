"""Bridge Streamlit UIs to governed suffai core metrics (PRD-aligned)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st

from config.settings import ROOT_DIR, settings
from suffai.garments.store import GarmentStore
from suffai.ingest import load_source_collections, normalize_all, referential_integrity_report
from suffai.metrics.geo import geo_query, PIN_AREA_LABELS
from suffai.metrics.rfm import compute_rfm_snapshots, rfm_distribution


@st.cache_data(ttl=300, show_spinner="Loading governed analytics…")
def load_governed() -> dict:
    raw = load_source_collections(settings.DATA_DIR)
    norm = normalize_all(raw)
    rfm = compute_rfm_snapshots(norm["orders"], norm["stores"])
    return {
        "orders": norm["orders"],
        "lines": norm["order_commodity_lines"],
        "customers": norm["customers"],
        "stores": norm["stores"],
        "rfm": rfm,
        "integrity": referential_integrity_report(norm),
    }


def garment_store() -> GarmentStore:
    return GarmentStore(ROOT_DIR / "data_derived")


def rfm_summary_df() -> pd.DataFrame:
    data = load_governed()
    return pd.DataFrame(rfm_distribution(data["rfm"]))


def geo_dashboard(
    geo_ids: list[str] | None = None,
    rfm_segments: list[str] | None = None,
) -> dict:
    data = load_governed()
    return geo_query(
        data["orders"],
        data["lines"],
        data["rfm"],
        geo_ids=geo_ids or None,
        rfm_segments=rfm_segments or None,
    )


def brand_empty_state() -> dict:
    return garment_store().brand_coverage_summary()


def tiles_df(geo_result: dict) -> pd.DataFrame:
    return pd.DataFrame(geo_result.get("tiles") or [])


def commodities_df(geo_result: dict) -> pd.DataFrame:
    return pd.DataFrame(geo_result.get("commodities") or [])


def area_options() -> list[str]:
    data = load_governed()
    pins = sorted({o.get("order_pin") for o in data["orders"] if o.get("order_pin")})
    return [f"{PIN_AREA_LABELS.get(p, p)} ({p})" for p in pins]


def parse_area_selection(labels: list[str]) -> list[str]:
    pins = []
    for lab in labels:
        if "(" in lab and lab.endswith(")"):
            pins.append(lab[lab.rfind("(") + 1 : -1])
        else:
            pins.append(lab)
    return pins
