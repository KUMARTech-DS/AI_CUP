"""SKU Ranking API (PRD/ERD v1.2): scoring, privacy, consent, validation, tenant scoping."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.main import app
from suffai.garments.store import GarmentStore
from suffai.metrics import sku_rank as sr

K = 3
PIN = "560038"
T_END = datetime(2026, 9, 30, tzinfo=timezone.utc)
client = TestClient(app)


def _g(cid, *, commodity="tshirt", color="navy", pattern="solid", gender="male", pin=PIN, days_ago=10, status="completed"):
    return {
        "source_customer_id": cid,
        "order_pin_code": pin,
        "commodity_type": commodity,
        "primary_color": color,
        "pattern": pattern,
        "gender": gender,
        "style_tags": ["casual"],
        "material_type": "cotton",
        "review_status": status,
        "observed_at": (T_END - timedelta(days=days_ago)).isoformat(),
    }


def _garments():
    rows = []
    for i in range(8):
        rows.append(_g(f"n{i}", days_ago=10 + i * 5))
    for i in range(4):
        rows.append(_g(f"n{i}", days_ago=120 + i))
    for i in range(4):
        rows.append(_g(f"j{i}", commodity="jeans", color="blue", pattern="solid", days_ago=100 + i * 10))
    rows.append(_g("x0", commodity="jacket", color="red", pin="560066"))
    rows.append(_g("gone", status="revoked"))
    return rows


def _ctx(garments=None, *, consent=None, mode="opt_out", lines=None, orders=None, customers=None, rfm=None):
    return sr.build_context(
        pincode=PIN,
        garments=_garments() if garments is None else garments,
        lines=lines or [],
        orders=orders or [],
        customers_by_id=customers or {},
        rfm_snapshots=rfm or [],
        consent_records=consent or [],
        consent_mode=mode,
        k=K,
    )


def _attrs(commodity="tshirt", color="navy", pattern="solid", gender="male", conf=0.9, material="cotton"):
    ev = {n: {"confidence": conf} for n in ("commodity_type", "primary_color", "material_type", "pattern", "gender")}
    return {"commodity_type": commodity, "primary_color": color, "pattern": pattern, "gender": gender,
            "material_type": material, "style_tags": ["casual"], "attribute_evidence": ev, "source": "test"}


def _items(*attrs):
    return [{"input_index": i, "image_id": f"img_{i}", "attributes": a} for i, a in enumerate(attrs)]


def test_prd_weight_example_and_renormalisation():
    score, eff, contrib = sr.combine({"visual_relevance": 90, "local_adoption": 80, "trend_momentum": 95, "seasonal_relevance": 70})
    assert score == 86.0
    assert contrib == {"visual_relevance": 36.0, "local_adoption": 24.0, "trend_momentum": 19.0, "seasonal_relevance": 7.0}
    score, eff, contrib = sr.combine({"visual_relevance": 90, "local_adoption": 80, "trend_momentum": 95, "seasonal_relevance": None})
    assert score == round((36 + 24 + 19) / 0.9, 1)
    assert eff["seasonal_relevance"] == 0 and abs(sum(eff.values()) - 1) < 1e-9
    assert contrib["seasonal_relevance"] is None
    assert sr.combine({n: None for n in sr.WEIGHTS})[0] is None


def test_similar_sku_outranks_dissimilar_and_scores_are_absolute():
    ctx = _ctx()
    ranked = sr.rank_skus(_items(_attrs(commodity="saree", color="pink", pattern="printed", gender="female"), _attrs()), ctx)
    assert [r["input_index"] for r in ranked] == [1, 0]
    assert [r["rank"] for r in ranked] == [1, 2]
    top = ranked[0]
    assert top["evidence"]["evidence_scope"] == "pin_garments"
    assert top["factor_scores"]["visual_relevance"] > ranked[1]["factor_scores"]["visual_relevance"] or ranked[1]["opportunity_score"] is None
    alone = sr.rank_skus(_items(_attrs()), ctx)[0]
    assert alone["rank"] == 1
    assert alone["opportunity_score"] == top["opportunity_score"]
    total = sum(v for v in top["factor_contributions"].values() if v is not None)
    assert abs(total - top["opportunity_score"]) < 0.1


def test_explainability_payload_fields():
    r = sr.rank_skus(_items(_attrs()), _ctx())[0]
    for key in ("input_index", "image_id", "rank", "opportunity_score", "factor_scores", "factor_contributions",
                "confidence", "evidence", "justification", "missing_signals", "limitations"):
        assert key in r
    ev = r["evidence"]
    for key in ("eligible_unique_customers", "observation_count", "trend_windows", "evidence_scope", "data_as_of"):
        assert key in ev
    assert ev["eligible_unique_customers"] == 8
    assert "8 of 12 eligible customers" in r["justification"]
    assert "not a sales forecast" in r["limitations"][0]


def test_deterministic_and_tie_break_by_input_index():
    ctx = _ctx()
    a = sr.rank_skus(_items(_attrs(), _attrs(), _attrs(color="blue", commodity="jeans")), ctx)
    b = sr.rank_skus(_items(_attrs(), _attrs(), _attrs(color="blue", commodity="jeans")), ctx)
    assert [(r["input_index"], r["opportunity_score"]) for r in a] == [(r["input_index"], r["opportunity_score"]) for r in b]
    dupes = [r for r in a if r["input_index"] in (0, 1)]
    assert dupes[0]["opportunity_score"] == dupes[1]["opportunity_score"]
    assert dupes[0]["input_index"] == 0 and dupes[0]["rank"] < dupes[1]["rank"]


def test_small_cohorts_suppressed_and_insufficient_evidence():
    sparse = [_g("a"), _g("b")]
    r = sr.rank_skus(_items(_attrs()), _ctx(sparse))[0]
    assert r["confidence"] == "insufficient_evidence"
    assert r["opportunity_score"] is None
    assert {m["factor"] for m in r["missing_signals"]} >= {"visual_relevance", "local_adoption", "trend_momentum"}
    ctx = _ctx()
    kurta = sr.rank_skus(_items(_attrs(commodity="kurta", color="green")), ctx)[0]
    assert kurta["evidence"]["adoption"]["suppressed"] is True
    assert kurta["evidence"]["eligible_unique_customers"] is None


def test_consent_revocation_and_opt_in():
    revoked = [{"source_customer_id": f"n{i}", "status": "revoked", "revoked_at": "2026-09-01T00:00:00+00:00"} for i in range(8)]
    ctx = _ctx(consent=revoked)
    assert ctx.pin_garments.customers == 4
    r = sr.rank_skus(_items(_attrs()), ctx)[0]
    assert r["evidence"]["adoption"]["suppressed"] is True
    granted = [{"source_customer_id": f"j{i}", "status": "granted", "granted_at": "2026-01-01T00:00:00+00:00"} for i in range(4)]
    assert _ctx(consent=granted, mode="opt_in").pin_garments.customers == 4
    assert _ctx(mode="opt_in").pin_garments.customers == 0


def test_service_line_fallback_and_segments():
    orders = [{"order_id": f"o{i}", "order_status": "delivered_to_customer", "payment_status": "paid"} for i in range(6)]
    lines = [
        {"order_id": f"o{i}", "order_pin": PIN, "customer_id": f"c{i}", "commodity_name": "Top Wear",
         "quantity": 2, "ordered_at": T_END - timedelta(days=5 + i * 20)}
        for i in range(6)
    ]
    customers = {f"c{i}": {"age": 28, "gender": "Female"} for i in range(6)}
    rfm = [{"customer_id": f"c{i}", "rfm_segment": "Champions"} for i in range(6)]
    ctx = _ctx([], lines=lines, orders=orders, customers=customers, rfm=rfm)
    r = sr.rank_skus(_items(_attrs()), ctx)[0]
    assert r["evidence"]["evidence_scope"] == "pin_service_lines"
    assert "visual_relevance" in {m["factor"] for m in r["missing_signals"]}
    assert r["opportunity_score"] is not None
    assert r["evidence"]["target"]["segments"][0] == {"age_band": "25_34", "gender": "female", "customers": 6, "share": 1.0}
    assert r["evidence"]["target"]["rfm_segment"]["segment"] == "Champions"
    assert any("service order lines" in lim for lim in r["limitations"])


def test_seasonal_rules():
    ctx = _ctx()
    hoodie = sr.score_sku(_attrs(commodity="hoodie", material="fleece"), ctx)
    assert hoodie["evidence"]["season"]["rule"] == "winter_wear"
    assert hoodie["factor_scores"]["seasonal_relevance"] == sr.SEASON_PROFILES["winter_wear"]["monsoon"]
    unreliable = sr.score_sku(_attrs(conf=0.1), ctx)
    assert unreliable["factor_scores"]["seasonal_relevance"] is None


# ---------- API ----------

def _jpeg(color=(29, 42, 77), size=(64, 80)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def _files(n, prefix="navy_tshirt"):
    return [("images", (f"{prefix}_{i}.jpg", _jpeg((20 + i * 9 % 200, 40, 77)), "image/jpeg")) for i in range(n)]


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    store = GarmentStore(tmp_path)
    monkeypatch.setattr("api.routers.sku_rank.get_garment_store", lambda: store)
    monkeypatch.setattr("api.routers.sku_rank._garment_history", _garments)
    monkeypatch.setattr("api.routers.sku_rank.PRIVACY_K", K)
    monkeypatch.setattr("api.routers.sku_rank.build_context", lambda **kw: sr.build_context(**{**kw, "k": K}))
    return store, _auth()


def _auth():
    tok = client.post("/v1/auth/brand/login", json={"brand": "Zara", "password": "brand123"}).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.parametrize("n", [1, 2, 20])
def test_rank_returns_exactly_n_unique_ranks(api_env, n):
    _, h = api_env
    r = client.post("/api/v1/sku/rank", files=_files(n), data={"pincode": PIN}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    results = d["ranked_results"]
    assert len(results) == n
    assert sorted(x["rank"] for x in results) == list(range(1, n + 1))
    assert sorted(x["input_index"] for x in results) == list(range(n))
    scores = [x["opportunity_score"] for x in results if x["opportunity_score"] is not None]
    assert scores == sorted(scores, reverse=True)
    for key in ("run_id", "pincode", "data_as_of", "scoring_version", "ranked_results"):
        assert key in d
    assert d["scoring_version"] == sr.SCORING_VERSION
    assert all(x["image_id"].startswith("img_") for x in results)


@pytest.mark.parametrize("n", [0, 21])
def test_rank_rejects_bad_image_counts(api_env, n):
    _, h = api_env
    r = client.post("/api/v1/sku/rank", files=_files(n) or None, data={"pincode": PIN}, headers=h)
    assert r.status_code == 422
    assert r.json()["errors"][0]["code"] == "INVALID_IMAGE_COUNT"


@pytest.mark.parametrize("pin", ["", "56003", "060038", "56003A", "5600381"])
def test_rank_rejects_malformed_pin(api_env, pin):
    _, h = api_env
    r = client.post("/api/v1/sku/rank", files=_files(1), data={"pincode": pin}, headers=h)
    assert r.status_code == 422
    assert r.json()["errors"][0]["code"] == "INVALID_PINCODE"


def test_rank_rejects_bad_lookback(api_env):
    _, h = api_env
    for value in ("abc", "3", "9999"):
        r = client.post("/api/v1/sku/rank", files=_files(1), data={"pincode": PIN, "lookback_days": value}, headers=h)
        assert r.status_code == 422


def test_rank_media_type_size_and_decode_errors(api_env, monkeypatch):
    _, h = api_env
    gif = [("images", ("a.gif", b"GIF89a" + b"\x00" * 50, "image/gif"))]
    assert client.post("/api/v1/sku/rank", files=gif, data={"pincode": PIN}, headers=h).status_code == 415
    corrupt = [("images", ("a.jpg", b"\xff\xd8\xff" + b"\x00" * 100, "image/jpeg"))]
    r = client.post("/api/v1/sku/rank", files=corrupt, data={"pincode": PIN}, headers=h)
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "UNPROCESSABLE_IMAGE"
    garbage = [("images", ("a.jpg", b"not an image", "image/jpeg"))]
    assert client.post("/api/v1/sku/rank", files=garbage, data={"pincode": PIN}, headers=h).status_code == 422
    monkeypatch.setattr("suffai.garments.sku_vision.MAX_IMAGE_BYTES", 200)
    monkeypatch.setattr("api.routers.sku_rank.MAX_IMAGE_BYTES", 200)
    r = client.post("/api/v1/sku/rank", files=_files(1), data={"pincode": PIN}, headers=h)
    assert r.status_code == 413


def test_rank_upstream_failure_returns_503(api_env, monkeypatch):
    from suffai.garments import sku_vision

    _, h = api_env

    def vertex_down(_img):
        raise sku_vision.UpstreamUnavailable("Vertex AI attribute extraction unavailable")

    monkeypatch.setattr(sku_vision, "default_extractor", lambda: vertex_down)
    r = client.post("/api/v1/sku/rank", files=_files(2), data={"pincode": PIN}, headers=h)
    assert r.status_code == 503
    assert r.json()["errors"][0]["code"] == "UPSTREAM_UNAVAILABLE"

    def bq_down():
        raise RuntimeError("bq down")

    monkeypatch.setattr(sku_vision, "default_extractor", lambda: sku_vision.local_attributes)
    monkeypatch.setattr("api.routers.sku_rank._garment_history", bq_down)
    assert client.post("/api/v1/sku/rank", files=_files(1), data={"pincode": PIN}, headers=h).status_code == 503


def test_vertex_failure_maps_to_upstream(monkeypatch):
    from suffai.garments import sku_vision

    img = sku_vision.validate_image(0, "x.jpg", "image/jpeg", _jpeg())

    def broken(data, content_type):
        raise RuntimeError("quota")

    import worker.vertex_extract as ve

    monkeypatch.setattr(ve, "extract_attributes_from_bytes", broken)
    with pytest.raises(sku_vision.UpstreamUnavailable):
        sku_vision.vertex_attributes(img)


def test_duplicate_inputs_keep_identity(api_env):
    _, h = api_env
    data = _jpeg()
    files = [("images", ("navy_tshirt.jpg", data, "image/jpeg")), ("images", ("navy_tshirt_copy.jpg", data, "image/jpeg"))]
    results = client.post("/api/v1/sku/rank", files=files, data={"pincode": PIN}, headers=h).json()["data"]["ranked_results"]
    by_index = {r["input_index"]: r for r in results}
    assert by_index[0]["image_id"] == by_index[1]["image_id"]
    assert by_index[1]["duplicate_of"] == 0 and by_index[0]["duplicate_of"] is None
    assert by_index[0]["rank"] == 1 and by_index[1]["rank"] == 2


def test_tenant_identity_comes_from_token_not_client(api_env):
    store, h = api_env
    r = client.post(
        "/api/v1/sku/rank",
        files=_files(1),
        data={"pincode": PIN, "brand": "Nike", "tenant_id": "tenant-nike", "brand_id": "nike"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["data"]["brand"] == "Zara"
    audit = [a for a in store._load("audit_events") if a["action"] == "sku_rank_run"]
    assert audit and audit[-1]["detail"]["tenant_id"] == "tenant-zara"
    assert audit[-1]["detail"]["brand"] == "Zara"
    assert "images" not in audit[-1]["detail"]


def test_rank_requires_auth_outside_local(api_env, monkeypatch):
    monkeypatch.setattr("api.deps.settings.ENVIRONMENT", "staging")
    r = client.post("/api/v1/sku/rank", files=_files(1), data={"pincode": PIN})
    assert r.status_code == 401
    r = client.post("/api/v1/sku/rank", files=_files(1), data={"pincode": PIN}, headers={"Authorization": "Bearer forged"})
    assert r.status_code == 401


def test_response_has_no_pii(api_env):
    _, h = api_env
    body = client.post("/api/v1/sku/rank", files=_files(2), data={"pincode": PIN}, headers=h).text
    for marker in ("phone", "address", "email", "customer_name", "source_customer_id"):
        assert marker not in body


def test_gcp_wiring_for_in_process_vertex():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert "COPY worker ./worker" in (root / "Dockerfile.api").read_text()
    assert "google-cloud-aiplatform" in (root / "requirements.txt").read_text()
    deploy = (root / "deploy" / "deploy_cloud_run.sh").read_text()
    api_roles = next(line for line in deploy.splitlines() if line.startswith("for role") and "pubsub.publisher" in line)
    assert "roles/aiplatform.user" in api_roles
    assert "--timeout=300" in deploy and "ENABLE_VERTEX=${API_ENABLE_VERTEX:-true}" in deploy
    assert sorted(p.name for p in (root / "db" / "migrations").glob("*.sql"))[-1].startswith("005")


def test_areas_endpoint():
    d = client.get("/api/v1/sku/areas", headers=_auth()).json()["data"]
    assert d["max_images"] == 20
    assert all(a["active_customers"] >= 30 for a in d["areas"])
