"""Unit tests for worker helpers that do not call GCP."""

from worker.result_utils import decide_review_status, min_confidence, normalize_result, parse_json_response


def test_parse_json_embedded():
    raw = parse_json_response('prefix {"commodity_type":"shirt","brand_name":"unknown"} suffix')
    assert raw["commodity_type"] == "shirt"


def test_normalize_and_review_status_needs_review():
    result = normalize_result(
        {
            "commodity_type": "shirt",
            "material_type": "unknown",
            "primary_color": "navy",
            "brand_name": "unknown",
            "attribute_evidence": {
                "commodity_type": {"source": "visual_inference", "confidence": 0.7},
                "material_type": {"source": "care_label_ocr", "confidence": 0.2},
                "primary_color": {"source": "visual_inference", "confidence": 0.8},
                "brand_name": {"source": "unknown", "confidence": 0.0},
            },
        },
        model_version="gemini-test",
    )
    assert min_confidence(result) < 0.9
    assert decide_review_status(result) == "NEEDS_REVIEW"


def test_normalize_high_confidence_completed():
    result = normalize_result(
        {
            "commodity_type": "shirt",
            "material_type": "cotton",
            "primary_color": "white",
            "brand_name": "unknown",
            "size_normalized": "M",
            "attribute_evidence": {
                "commodity_type": {"confidence": 0.95},
                "material_type": {"confidence": 0.96},
                "primary_color": {"confidence": 0.94},
                "brand_name": {"confidence": 0.1},
                "size_normalized": {"confidence": 0.93},
            },
        },
        model_version="gemini-test",
    )
    assert decide_review_status(result) == "COMPLETED"
