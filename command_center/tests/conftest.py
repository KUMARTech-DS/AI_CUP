import sys
from pathlib import Path

import pytest

CC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CC_DIR))
sys.path.insert(0, str(CC_DIR / "scripts"))


@pytest.fixture(autouse=True)
def _isolate_geo_grounding(monkeypatch):
    """No grounded Gemini calls or Cloud SQL caches in tests unless a test installs its own stub."""
    import gemini_grounded
    import geo_market
    import geo_proximity

    def _offline(prompt):
        raise gemini_grounded.GroundingUnavailable("offline in tests")

    monkeypatch.setattr(gemini_grounded, "_override", _offline)
    monkeypatch.setattr(geo_proximity, "_db_enabled", lambda: False)
    geo_proximity.clear_cache()
    geo_market.clear_cache()
    yield
    geo_proximity.clear_cache()
    geo_market.clear_cache()
