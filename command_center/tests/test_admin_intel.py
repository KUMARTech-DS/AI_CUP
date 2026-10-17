"""Admin four-tab intelligence: masking, search, aggregates, status mapping, auth."""

from __future__ import annotations

import logging
from datetime import date

import pytest

import admin_intel as ai

CUSTOMERS = [
    {"_id": "cust-aaaa-0001", "name": "Priya Sharma", "number": "+91 98450 12210", "age": 29, "gender": "female",
     "pin_code": "560038", "rfm_segment": "Champions"},
    {"_id": "cust-bbbb-0002", "name": "Rahul Verma", "number": "9123456789", "age": 41, "gender": "male",
     "pin_code": "560001", "rfm_segment": "At Risk"},
    {"_id": "cust-cccc-0003", "name": "Anita Rao", "number": None, "age": None, "gender": "female",
     "pin_code": "560038", "rfm_segment": "Champions"},
]
ORDERS = [
    {"_id": "o1", "customer_id": "cust-aaaa-0001", "scheduled_date": "2026-07-05", "service": "Dry Clean",
     "total_number_of_clothes": 3, "amount": 1500},
    {"_id": "o2", "customer_id": "cust-aaaa-0001", "scheduled_date": "2026-08-10", "service": "Wash & Fold",
     "total_number_of_clothes": 5, "amount": 2000},
    {"_id": "o3", "customer_id": "cust-aaaa-0001", "scheduled_date": "2026-09-20", "service": "Dry Clean",
     "total_number_of_clothes": 2, "amount": 2500},
    {"_id": "o4", "customer_id": "cust-bbbb-0002", "scheduled_date": "2026-03-01", "service": "Iron",
     "total_number_of_clothes": 4, "amount": 400},
    {"_id": "o5", "customer_id": "cust-bbbb-0002", "scheduled_date": "2026-03-15", "service": "Iron",
     "total_number_of_clothes": 4, "amount": 400},
    {"_id": "o6", "customer_id": "cust-bbbb-0002", "scheduled_date": "2026-04-01", "service": "Iron",
     "total_number_of_clothes": 4, "amount": 400},
]


def _g(cid, gid, name, brand, color, pattern, size, material, gender="female"):
    return {"customer_id": cid, "order_id": "o", "commodity_id": gid, "name": name, "brand": brand, "color": color,
            "pattern": pattern, "size": size, "material": material, "gender": gender}


COMMODITIES = [
    _g("cust-aaaa-0001", "g1", "Kurta", "FabIndia", "Red", "Solid", "M", "Cotton"),
    _g("cust-aaaa-0001", "g2", "Kurta", "FabIndia", "Red", "Floral", "M", "Cotton"),
    _g("cust-aaaa-0001", "g3", "Dress", "Zara", "Black", "Solid", "S", "Silk"),
    _g("cust-bbbb-0002", "g4", "Shirt", "Zara", "Blue", "Striped", "L", "Cotton", "male"),
    _g("cust-bbbb-0002", "g5", "Shirt", "Allen Solly", "White", "Solid", "XL", "Linen", "male"),
    _g("cust-cccc-0003", "g6", "Dress", "FabIndia", "", "unknown", "", "Cotton"),
]


@pytest.fixture(autouse=True)
def fixture_data(monkeypatch):
    monkeypatch.setattr(ai, "_dataset_loader", lambda: (CUSTOMERS, ORDERS, COMMODITIES))
    ai.clear_cache()
    yield
    ai.clear_cache()


def test_mask_phone_hides_middle_digits():
    assert ai.mask_phone("+91 98450 12210") == "+91 98xxx xx210"
    assert ai.mask_phone("") == "—"
    assert ai.mask_phone("123") == "xxxx"


def test_search_by_name_and_phone_digits():
    assert [c["name"] for c in ai.customers_page(q="priya")["items"]] == ["Priya Sharma"]
    assert [c["name"] for c in ai.customers_page(q="98450")["items"]] == ["Priya Sharma"]
    assert [c["name"] for c in ai.customers_page(q="+91 912")["items"]] == ["Rahul Verma"]
    assert ai.customers_page(q="nobody")["total"] == 0


def test_page_rows_never_carry_raw_phone():
    items = ai.customers_page()["items"]
    blob = repr(items)
    assert "9845012210" not in blob and "9123456789" not in blob
    assert items[0]["phone_masked"] == "+91 98xxx xx210"


def test_qualified_rule_and_overview_filters():
    o = ai.customers_overview()
    # Priya: 3 orders, latest on the dataset max date. Rahul: 3 orders but stale.
    assert o["total_customers"] == 3 and o["qualified_users"] == 1
    champs = ai.customers_overview(rfm=["champions"])
    assert champs["cohort_size"] == 2
    assert ai.customers_overview(pins=["560001"])["cohort_size"] == 1
    assert {r["value"] for r in o["rfm_breakdown"]} == {"Champions", "At Risk"}
    assert ai.customers_page(qualified_only=True)["total"] == 1


def test_customer_detail_series():
    d = ai.customer_detail("cust-aaaa-0001")
    assert d["demographics"]["ltv"] == 6000 and d["demographics"]["aov"] == 2000
    assert d["top_commodities"][0] == {"value": "Kurta", "count": 2, "share": 66.7}
    assert d["colors"][0]["value"] == "Red"
    assert [s["value"] for s in d["sizes"]] == ["S", "M"]
    assert {s["value"] for s in d["service_split"]} == {"Dry Clean", "Wash & Fold"}
    assert [m["month"] for m in d["momentum"]] == ["2026-07", "2026-08", "2026-09"]
    assert d["momentum"][-1]["cumulative_spend"] == 6000
    assert "phone_digits" not in d and "9845012210" not in repr(d)
    assert ai.customer_detail("missing") is None


def test_missing_fields_stay_empty_not_invented():
    d = ai.customer_detail("cust-cccc-0003")
    assert d["orders"] == 0 and d["momentum"] == [] and d["colors"] == [] and d["sizes"] == []
    assert d["has_phone"] is False


def test_garments_filters_and_aggregates():
    g = ai.garments()
    assert g["total"] == 6 and g["total_tracked"] == 6
    assert g["top_fabrics"][0]["value"] == "Cotton"
    assert "unknown" not in {p["value"] for p in g["patterns"]}
    assert [s["value"] for s in g["size_curve"]] == ["S", "M", "L", "XL"]
    zara = ai.garments(filters={"brand": ["zara"]})
    assert zara["total"] == 2 and {i["commodity"] for i in zara["items"]} == {"Dress", "Shirt"}
    assert ai.garments(filters={"material": ["cotton"], "size": ["M"]})["total"] == 2
    assert zara["items"][0]["owner_id"] == "cust-aaa"
    assert "Cotton" in g["commodity_fabric"]["series"]


def test_brands_aggregates_and_filters():
    b = ai.brands()
    assert b["ranking"][0] == {"brand": "FabIndia", "items": 3, "share": 50.0, "unique_customers": 2}
    fab = next(x for x in b["brands"] if x["brand"] == "FabIndia")
    assert fab["top_commodities"][0] == "Kurta"
    ov = b["overlap"]
    i, j = ov["brands"].index("FabIndia"), ov["brands"].index("Zara")
    assert ov["matrix"][i][j] == 1  # Priya owns both
    assert {n["name"] for n in b["sunburst"]} == {"Women", "Men"}
    assert ai.brands(pins=["560001"])["total_items"] == 2
    assert ai.brands(rfm=["at risk"], brand_list=["zara"])["total_items"] == 1
    assert ai.brands(menu=["men"])["brand_count"] == 2


TODAY = date(2026, 10, 18)


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"status": "launched", "start_date": "2026-10-01", "end_date": "2026-10-30"}, "Active"),
        ({"status": "launched", "start_date": "2026-08-01", "end_date": "2026-08-30"}, "Completed"),
        ({"status": "launched", "launched_at": "2026-09-01T10:00:00", "duration_days": 14}, "Completed"),
        ({"status": "launched", "launched_at": "2026-10-10T10:00:00"}, "Active"),
        ({"status": "configured", "start_date": "2026-11-01"}, "Scheduled"),
        ({"status": "configured", "start_date": "2026-10-01"}, "Draft"),
        ({"status": "ranked"}, "Draft"),
        ({"status": "ignored"}, "Discarded"),
    ],
)
def test_campaign_status_mapping(row, expected):
    assert ai.map_status(row, today=TODAY) == expected


def test_campaigns_simulated_metrics_and_filters(monkeypatch):
    rows = [
        {"campaign_id": "c1", "campaign_name": "Festive", "brand_name": "Zara", "status": "launched",
         "start_date": "2026-10-01", "end_date": "2026-10-30", "pin_codes": ["560038"], "rfm_segment": "Champions",
         "impact": {"reach": 1000, "conversions": 50}, "featured_commodities": ["Dress"]},
        {"campaign_id": "c2", "campaign_name": "Linen", "brand_name": "Allen Solly", "status": "ranked",
         "pin_codes": ["560001"], "rfm_segment": None},
    ]
    monkeypatch.setattr(ai, "_campaign_loader", lambda: rows)
    out = ai.campaigns(today=TODAY)
    assert out["total"] == 2
    c1 = next(x for x in out["items"] if x["campaign_id"] == "c1")
    assert c1["status"] == "Active" and c1["conversion_rate"] == 5.0 and c1["redemptions"] == 50
    c2 = next(x for x in out["items"] if x["campaign_id"] == "c2")
    assert c2["conversion_rate"] is None and c2["redemptions"] is None and c2["target_rfm"] == "All segments"
    s = out["summary"]
    assert s["active"] == 1 and s["avg_conversion_rate"] == 5.0 and s["total_redemptions"] == 50
    assert s["by_brand"]["Zara"]["status"]["Active"] == 1
    assert ai.campaigns(status=["draft"], today=TODAY)["total"] == 1
    assert ai.campaigns(pins=["560038"], today=TODAY)["items"][0]["campaign_id"] == "c1"
    assert ai.campaigns(brand=["allen solly"], today=TODAY)["total"] == 1


def test_dataset_load_failure_is_masked(monkeypatch):
    def boom():
        raise RuntimeError("bq creds /secret/path")

    monkeypatch.setattr(ai, "_dataset_loader", boom)
    ai.clear_cache()
    with pytest.raises(ai.IntelUnavailable) as exc:
        ai.customers_page()
    assert "secret" not in str(exc.value)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

SESSIONS = {
    "adm": {"role": "admin", "username": "ops-admin"},
    "brd": {"role": "brand", "username": "zara-user", "brand_name": "Zara"},
}


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import auth_acl
    import main

    monkeypatch.setattr(auth_acl, "get_session", lambda tok: SESSIONS.get(tok))
    monkeypatch.setattr(main, "_proxy_similarity_get", lambda path, params: {"kpis": {}})
    return TestClient(main.app)


ADMIN_GETS = [
    "/v1/admin/intel/customers",
    "/v1/admin/intel/customers/cust-aaaa-0001",
    "/v1/admin/intel/customers-overview",
    "/v1/admin/intel/garments",
    "/v1/admin/intel/brands",
    "/v1/admin/unique-summary",
    "/v1/admin/unique-garments",
]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_admin_routes_reject_brand_and_anonymous(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Session-Token": "brd"}).status_code == 403


def test_reveal_rejects_brand(client):
    r = client.post("/v1/admin/intel/customers/cust-aaaa-0001/reveal-phone", headers={"X-Session-Token": "brd"})
    assert r.status_code == 403


def test_admin_routes_no_store_and_masked(client):
    h = {"X-Session-Token": "adm"}
    r = client.get("/v1/admin/intel/customers", params={"q": "priya"}, headers=h)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json()["items"][0]["phone_masked"] == "+91 98xxx xx210"
    r = client.get("/v1/admin/intel/garments", params={"brand": "Zara,FabIndia", "garment_size": "M"}, headers=h)
    assert r.json()["total"] == 2
    assert client.get("/v1/admin/intel/customers/nope", headers=h).status_code == 404


def test_reveal_phone_logs_without_number(client, caplog):
    caplog.set_level(logging.INFO, logger="command_center")
    r = client.post("/v1/admin/intel/customers/cust-aaaa-0001/reveal-phone", headers={"X-Session-Token": "adm"})
    assert r.status_code == 200 and r.json()["phone"] == "+91 98450 12210"
    assert r.headers["cache-control"] == "no-store"
    lines = [rec.getMessage() for rec in caplog.records if "reveal_phone" in rec.getMessage()]
    assert lines == ["admin=ops-admin customer=cust-aaa action=reveal_phone"]
    assert all("98450" not in rec.getMessage() for rec in caplog.records)


def test_reveal_phone_missing_number_is_404(client):
    r = client.post("/v1/admin/intel/customers/cust-cccc-0003/reveal-phone", headers={"X-Session-Token": "adm"})
    assert r.status_code == 404
