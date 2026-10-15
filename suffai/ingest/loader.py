"""Read-only loaders for the ten supplied Mongo-style JSON fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SOURCE_FILES: dict[str, str] = {
    "customers": "customers.json",
    "orders": "orders.json",
    "commodities": "commodities.json",
    "stores": "stores.json",
    "services": "services.json",
    "storeservices": "storeservices.json",
    "subscriptions": "subscriptions.json",
    "complaintorders": "complaintorders.json",
    "timeslots": "timeslots.json",
    "usercouponusages": "usercouponusages.json",
}


def _read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def load_source_collections(data_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Load all source collections without mutating originals.

    `data_dir` is a local directory or a gs://bucket/prefix location.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    location = str(data_dir)
    if location.startswith("gs://"):
        from suffai.gcp_io import read_gcs_json

        base = location.rstrip("/")
        for key, filename in SOURCE_FILES.items():
            out[key] = _as_list(read_gcs_json(f"{base}/{filename}"))
        return out
    root = Path(data_dir)
    for key, filename in SOURCE_FILES.items():
        out[key] = _read_json(root / filename)
    return out
