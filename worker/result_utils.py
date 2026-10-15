"""Pure helpers for extraction result normalization (no GCP imports)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from worker.settings import settings


def parse_json_response(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def normalize_result(raw: Dict[str, Any], *, model_version: str) -> Dict[str, Any]:
    evidence = raw.get("attribute_evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {}

    def field(name: str, default: str = "unknown"):
        val = raw.get(name)
        if val is None or val == "":
            return default
        return val

    gender = field("gender")
    if isinstance(gender, str):
        gender = gender.strip().lower()
    allowed_gender = {"male", "female", "unisex", "kids", "unknown"}
    if gender not in allowed_gender:
        gender = "unknown"

    return {
        "commodity_type": field("commodity_type"),
        "material_type": field("material_type"),
        "material_blend": raw.get("material_blend"),
        "primary_color": field("primary_color"),
        "secondary_colors": raw.get("secondary_colors") or [],
        "size_raw": raw.get("size_raw"),
        "size_normalized": raw.get("size_normalized"),
        "size_system": raw.get("size_system"),
        "brand_name": field("brand_name"),
        "brand_sku": raw.get("brand_sku"),
        "pattern": field("pattern"),
        "gender": gender,
        "style_tags": raw.get("style_tags") or [],
        "attribute_evidence": evidence,
        "notes": raw.get("notes"),
        "model_version": model_version,
        "raw_model_json": raw,
    }


def min_confidence(result: Dict[str, Any]) -> float:
    """Min confidence over core fields. Brand ignored when brand_name is unknown."""
    evidence = result.get("attribute_evidence") or {}
    keys = ["commodity_type", "material_type", "primary_color", "size_normalized"]
    brand = (result.get("brand_name") or "unknown").lower()
    if brand != "unknown":
        keys.append("brand_name")
    confs = []
    for key in keys:
        item = evidence.get(key) or {}
        if isinstance(item, dict) and item.get("confidence") is not None:
            try:
                confs.append(float(item["confidence"]))
            except (TypeError, ValueError):
                pass
    return min(confs) if confs else 0.0


def decide_review_status(result: Dict[str, Any]) -> str:
    brand = (result.get("brand_name") or "unknown").lower()
    brand_ev = (result.get("attribute_evidence") or {}).get("brand_name") or {}
    brand_conf = float(brand_ev.get("confidence") or 0)
    if brand != "unknown" and brand_conf < settings.AUTO_ACCEPT_CONFIDENCE:
        return "NEEDS_REVIEW"
    if min_confidence(result) >= settings.AUTO_ACCEPT_CONFIDENCE:
        return "COMPLETED"
    return "NEEDS_REVIEW"
