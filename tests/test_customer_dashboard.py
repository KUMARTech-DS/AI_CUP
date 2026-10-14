"""Customer profiles (customer_uuid = garment user_uuid) across dashboard APIs."""

import json

from fastapi.testclient import TestClient

from api.main import app
from suffai.garments.store import GarmentStore

client = TestClient(app)


def _headers():
    r = client.post("/v1/auth/brand/login", json={"brand": "Peter England", "password": "brand123"})
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _synthetic_customers(n: int):
    return {
        f"cust-{i}": {
            "customer_uuid": f"cust-{i}",
            "gender": "female" if i % 2 else "male",
            "age": 28,
            "pin": "560102",
            "device_type": "mobile",
            "phone_number": "9999999999",
            "address": "secret street",
        }
        for i in range(n)
    }


def test_geo_query_returns_demographics_and_filters():
    h = _headers()
    base = client.post("/v1/geo/query", json={"geo_level": "pin"}, headers=h).json()
    assert "demographics" in base["data"]
    assert "device_mix" in base["data"]["demographics"]

    filtered = client.post(
        "/v1/geo/query",
        json={"geo_level": "pin", "device_types": ["mobile"]},
        headers=h,
    ).json()
    if not filtered["errors"]:
        assert filtered["data"]["meta"]["eligible_orders"] <= base["data"]["meta"]["eligible_orders"]


def test_research_returns_customer_demographics():
    h = _headers()
    body = client.post("/v1/product-opportunities", json={"device_types": ["mobile"]}, headers=h).json()
    assert "customer_demographics" in body["data"]
    for opp in body["data"]["opportunities"]:
        assert "demographics" in opp


def test_campaign_eval_uses_target_gender():
    h = _headers()
    created = client.post(
        "/v1/campaigns",
        json={"attributes": {"commodity_type": "shirt", "target_gender": "male"}},
        headers=h,
    ).json()
    cid = created["data"]["campaign"]["campaign_id"]
    ev = client.post(f"/v1/campaigns/{cid}/evaluate", json={}, headers=h).json()
    scored = [e for e in ev["data"]["evaluations"] if not e.get("suppressed")]
    for e in scored:
        assert e["creative_tags"]["gender"] == "male"
        assert "gender_affinity" in e["components"]
        assert "demographics" in e


def test_brand_benchmarks_filter_by_customer_and_hide_pii(tmp_path, monkeypatch):
    customers = _synthetic_customers(80)
    store = GarmentStore(tmp_path)
    garments = [
        {
            "garment_instance_id": f"g-{i}",
            "source_customer_id": cid,
            "brand_name_raw": "Peter England",
            "commodity_type": "shirt",
            "review_status": "completed",
        }
        for i, cid in enumerate(customers)
    ]
    (tmp_path / "garment_instances.json").write_text(json.dumps(garments))
    monkeypatch.setattr("api.deps.get_garment_store", lambda: store)
    monkeypatch.setattr("api.routers.brands.get_customer_index", lambda: customers)

    h = _headers()
    all_rows = client.get("/v1/brands/peterengland/benchmarks", headers=h).json()["data"]
    assert all_rows["cohort_customers"] == 80
    demo = all_rows["customer_demographics"]
    assert {r["value"] for r in demo["gender_mix"]} == {"male", "female"}
    assert "9999999999" not in json.dumps(all_rows)
    assert "secret street" not in json.dumps(all_rows)

    female = client.get(
        "/v1/brands/peterengland/benchmarks?genders=female&device_types=mobile", headers=h
    ).json()["data"]
    assert female["cohort_customers"] == 40
