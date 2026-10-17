"""compare-pins scoring (no GCP / DB)."""

import sys
import types

import pytest

if "google.cloud.storage" not in sys.modules:
    try:
        import google.cloud.storage  # noqa: F401
    except ImportError:
        google = sys.modules.setdefault("google", types.ModuleType("google"))
        cloud = types.ModuleType("google.cloud")
        cloud.storage = types.ModuleType("google.cloud.storage")
        google.cloud = cloud
        sys.modules["google.cloud"] = cloud
        sys.modules["google.cloud.storage"] = cloud.storage

from similarity import compare  # noqa: E402

MODEL = "multimodalembedding@001"


def _rows():
    return [
        ("560008", "g-red", 10, [1.0, 0.0, 0.0], MODEL),
        ("560008", "g-blue", 2, [0.0, 1.0, 0.0], MODEL),
        ("560008", "g-opposite", 50, [-1.0, 0.0, 0.0], MODEL),
        ("110001", "g-mixed", 4, [1.0, 1.0, 0.0], MODEL),
        ("110001", "g-stub", 9, [1.0, 0.0, 0.0], "stub-" + MODEL),
        ("110001", "g-short", 9, [1.0, 0.0], MODEL),
    ]


def _loader(pins, limit):
    return [r for r in _rows() if r[0] in pins], {"560008": 62, "110001": 22}


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(compare.settings, "COMPARE_CACHE_SEC", 0)


def test_normalize_pins():
    assert compare.normalize_pins(["560008", " 560008 ", "110001", ""]) == ["560008", "110001"]
    with pytest.raises(ValueError):
        compare.normalize_pins([])
    with pytest.raises(ValueError):
        compare.normalize_pins(["5600"])


def test_candidates_skip_other_models_and_sizes():
    cands, totals = compare.load_candidates(["560008", "110001"], MODEL, 3, loader=_loader)
    assert {c[1] for c in cands} == {"g-red", "g-blue", "g-opposite", "g-mixed"}
    assert totals == {"560008": 62, "110001": 22}


def test_score_pins_shape_and_values():
    pins = ["560008", "110001", "400001"]
    cands, totals = compare.load_candidates(pins, MODEL, 3, loader=_loader)
    out = compare.score_pins([2.0, 0.0, 0.0], pins, cands, totals, topk=2)
    p = out["per_pin"]["560008"]
    assert [m["garment_id"] for m in p["top_matches"]] == ["g-red", "g-blue"]
    assert p["top_matches"][0] == {"garment_id": "g-red", "similarity": 1.0, "observation_count": 10}
    assert p["pin_fit"] == pytest.approx(10 / 12, abs=1e-4)
    assert p["total_observations"] == 62
    assert out["per_pin"]["110001"]["pin_fit"] == pytest.approx(0.7071, abs=1e-4)
    empty = out["per_pin"]["400001"]
    assert empty == {"pin_fit": 0.0, "total_observations": 0, "candidates": 0, "top_matches": []}
    expected = (p["pin_fit"] * 62 + 0.7071 * 22) / 84
    assert out["aggregate_fit"] == pytest.approx(expected, abs=1e-3)
    assert 0.0 <= out["aggregate_fit"] <= 1.0


def test_negative_similarity_floors_at_zero():
    cands, totals = compare.load_candidates(["560008"], MODEL, 3, loader=_loader)
    out = compare.score_pins([-1.0, 0.0, 0.0], ["560008"], cands, totals, topk=1)
    assert out["per_pin"]["560008"]["top_matches"][0]["garment_id"] == "g-opposite"
    out = compare.score_pins([0.0, 0.0, 1.0], ["560008"], cands, totals, topk=3)
    assert out["aggregate_fit"] == 0.0


def test_no_garments_anywhere():
    out = compare.score_pins([1.0, 0.0, 0.0], ["400001"], [], {}, topk=5)
    assert out["aggregate_fit"] == 0.0


def test_matches_command_center_contract():
    """Keys read by command_center brand_campaign.freq_coverage_from_compare / upsert_pin_scores."""
    pins = ["560008"]
    cands, totals = compare.load_candidates(pins, MODEL, 3, loader=_loader)
    detail = compare.score_pins([1.0, 0.0, 0.0], pins, cands, totals, topk=5)["per_pin"]["560008"]
    assert {"pin_fit", "top_matches", "total_observations"} <= set(detail)
    assert all({"observation_count"} <= set(m) for m in detail["top_matches"])
    assert all("brand_name" not in m for m in detail["top_matches"])


def test_cache_reuses_candidates(monkeypatch):
    monkeypatch.setattr(compare.settings, "COMPARE_CACHE_SEC", 60)
    compare._cache.clear()
    calls = []

    def loader(pins, limit):
        calls.append(tuple(pins))
        return _loader(pins, limit)

    compare.load_candidates(["560008"], MODEL, 3, loader=loader)
    compare.load_candidates(["560008"], MODEL, 3, loader=loader)
    assert len(calls) == 1
    compare._cache.clear()
