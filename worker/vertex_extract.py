"""Vertex AI Gemini garment / campaign attribute extraction."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import vertexai
from vertexai.generative_models import GenerationConfig, GenerativeModel, Part

from worker.gcs_io import GcsImage
from worker.result_utils import decide_review_status, min_confidence, normalize_result, parse_json_response
from worker.settings import settings

log = logging.getLogger("suffai-extraction.vertex")

EXTRACTION_SCHEMA_HINT = {
    "commodity_type": "shirt|tshirt|trouser|jeans|kurta|saree|dress|jacket|hoodie|home_linen|other|unknown",
    "material_type": "cotton|polyester|silk|wool|linen|denim|blend|unknown",
    "material_blend": {"cotton": 0.0, "polyester": 0.0},
    "primary_color": "normalized color name or unknown",
    "secondary_colors": ["..."],
    "size_raw": "text from label or null",
    "size_normalized": "S|M|L|XL|numeric|unknown|null",
    "size_system": "IN|US|EU|unknown|null",
    "brand_name": "canonical or unknown",
    "brand_sku": "null if not readable",
    "pattern": "solid|striped|checked|printed|other|unknown",
    "style_tags": ["casual", "..."],
    "attribute_evidence": {
        "commodity_type": {"source": "visual_inference|care_label_ocr|brand_label_ocr", "confidence": 0.0},
        "material_type": {"source": "care_label_ocr|visual_inference", "confidence": 0.0},
        "primary_color": {"source": "visual_inference", "confidence": 0.0},
        "size_normalized": {"source": "size_label_ocr|unknown", "confidence": 0.0},
        "brand_name": {"source": "brand_label_ocr|visual_inference|unknown", "confidence": 0.0},
    },
    "notes": "short abstention reasons if any",
}

SYSTEM_PROMPT = """You are a garment attribute extractor for laundry/fashion intelligence.
Return ONLY valid JSON matching the schema. Rules:
- Prefer care-label OCR for material composition; if unreadable use "unknown".
- Prefer brand/size label OCR for brand and size; do not invent SKUs.
- Image-only material guesses must use source visual_inference and lower confidence.
- Never infer customer gender, age, or income.
- unknown is a valid value. Do not hallucinate brands.
- confidence is 0.0–1.0 per field in attribute_evidence.
"""


def extract_attributes(image: GcsImage) -> Dict[str, Any]:
    """
    Call Vertex Gemini on the image.
    Prefers GCS URI (no re-upload); falls back to inline bytes if URI fails.
    """
    if not settings.ENABLE_VERTEX:
        raise RuntimeError("ENABLE_VERTEX is false")
    if not settings.PROJECT_ID:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT / GCP_PROJECT is required for Vertex")

    model_name = settings.VERTEX_MODEL
    vertexai.init(project=settings.PROJECT_ID, location=settings.VERTEX_LOCATION)
    model = GenerativeModel(model_name)

    prompt = (
        SYSTEM_PROMPT
        + "\nJSON schema example (types/enums only):\n"
        + json.dumps(EXTRACTION_SCHEMA_HINT)
        + "\nExtract attributes from this garment/campaign product image."
    )
    gen_cfg = GenerationConfig(
        temperature=0.1,
        max_output_tokens=2048,
        response_mime_type="application/json",
    )

    try:
        part = Part.from_uri(image.gs_uri, mime_type=image.content_type)
        response = model.generate_content([prompt, part], generation_config=gen_cfg)
    except Exception as uri_exc:
        log.warning("Vertex GCS URI failed (%s); retrying with inline bytes", uri_exc)
        part = Part.from_data(data=image.data, mime_type=image.content_type)
        response = model.generate_content([prompt, part], generation_config=gen_cfg)

    text = getattr(response, "text", None) or ""
    if not text and getattr(response, "candidates", None):
        chunks = []
        for cand in response.candidates:
            for p in getattr(cand.content, "parts", []) or []:
                if getattr(p, "text", None):
                    chunks.append(p.text)
        text = "\n".join(chunks)

    parsed = parse_json_response(text)
    result = normalize_result(parsed, model_version=model_name)
    result["review_status"] = decide_review_status(result)
    log.info(
        "vertex_ok model=%s review_status=%s min_conf=%.2f brand=%s",
        model_name,
        result["review_status"],
        min_confidence(result),
        result.get("brand_name"),
    )
    return result
