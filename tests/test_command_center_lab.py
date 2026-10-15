from datetime import datetime, timedelta

from suffai.ingest.normalize import geo_cell, normalize_complaints
from suffai.metrics.command_center import command_center, garment_profile, haversine_km
from suffai.metrics.research_lab import research_lab, silhouette_for

K = 3
T0 = datetime(2026, 1, 1)


def _order(oid, cid, pin, store, *, status="delivered_to_customer", amount=500.0, day=0):
    return {
        "order_id": oid,
        "customer_id": cid,
        "order_pin": pin,
        "store_id": store,
        "order_status": status,
        "payment_status": "paid",
        "total_amount": amount,
        "ordered_at": T0 + timedelta(days=day),
    }


def _garment(cid, pin, *, size="M", color="navy", brand="Zara", status="completed"):
    return {
        "source_customer_id": cid,
        "order_pin_code": pin,
        "size_normalized": size,
        "primary_color": color,
        "material_type": "cotton",
        "commodity_type": "t-shirt",
        "pattern": "solid",
        "style_tags": ["casual"],
        "brand_name_raw": brand,
        "review_status": status,
    }


def _fixture():
    customers = [
        {"customer_id": f"c{i}", "home_lat_cell": 12.97 + 0.001 * i, "home_lng_cell": 77.64} for i in range(5)
    ] + [{"customer_id": "lonely", "home_lat_cell": 13.10, "home_lng_cell": 77.50}]
    orders = [_order(f"o{i}", f"c{i}", "560038", "s1", day=i * 20) for i in range(5)]
    orders += [_order("o5", "c0", "560038", "s2", day=90)]
    orders += [_order("ox", "c1", "560038", "s1", status="cancelled", day=10)]
    orders += [_order("o9", "lonely", "560064", "s2", day=5)]
    lines = [{"order_id": o["order_id"], "order_pin": o["order_pin"], "commodity_name": "Shirt", "quantity": 2} for o in orders]
    stores = [
        {"store_id": "s1", "store_name": "Indiranagar 1", "area": "Indiranagar", "pin_code": "560038", "status": "active"},
        {"store_id": "s2", "store_name": "Yelahanka 1", "area": "Yelahanka", "pin_code": "560064", "status": "active"},
    ]
    complaints = [{"complaint_id": "x1", "order_id": "o1"}, {"complaint_id": "x2", "order_id": "o9"}]
    garments = [_garment(f"c{i}", "560038", size="L" if i % 2 else "M") for i in range(4)]
    garments.append(_garment("c4", "560038", brand="MyBrand", status="needs_review"))
    return dict(orders=orders, lines=lines, stores=stores, customers=customers, complaints=complaints, garments=garments)


def test_geo_cell_snaps_and_rejects_bad_input():
    assert geo_cell("geo:12.97163,77.59456") == (12.97, 77.59)
    assert geo_cell("12 MG Road") == (None, None)
    assert geo_cell("geo:0,0") == (None, None)
    assert geo_cell("geo:abc,1") == (None, None)
    assert geo_cell(None) == (None, None)


def test_normalize_complaints_extracts_ids():
    rows = normalize_complaints([
        {"_id": {"$oid": "c1"}, "order_id": {"$oid": "o1"}, "resolved": 1, "complaint_status": "open"},
        {"order_id": "o2"},
    ])
    assert len(rows) == 1
    assert rows[0]["complaint_id"] == "c1" and rows[0]["order_id"] == "o1" and rows[0]["resolved"] is True


def test_command_center_hubs_suppression_and_metrics():
    out = command_center(**_fixture(), polygons={}, my_brand="MyBrand", k=K)
    hubs = {h["pin_code"]: h for h in out["hubs"]}

    small = hubs["560064"]
    assert small["suppressed"] is True
    assert "orders_per_month" not in small and "garments" not in small

    hub = hubs["560038"]
    assert hub["suppressed"] is False and hub["rank"] == 1 and hub["grade"] == "A+"
    assert hub["location_precision"] == "customer_cells" and hub["lat"] is not None
    assert hub["active_customers"] == 5
    assert hub["served_local_pct"] == round(5 / 6, 4)
    assert hub["cancel_rate"] == round(1 / 7, 4)
    assert hub["complaints"] == 1
    assert hub["nearest_store"]["store_name"] == "Indiranagar 1"
    assert [r["radius_km"] for r in hub["catchment"]] == [1.0, 2.2, 3.8]
    assert hub["catchment"][0]["customers"] == 5
    assert hub["commodities"][0]["value"] == "Shirt"

    assert out["kpis"]["hubs"] == 1
    assert {s["store_id"] for s in out["stores"]} == {"s1"}  # s2's PIN has no centroid
    assert out["heat"] and all(c["customers"] >= K for c in out["heat"])
    assert all("customer_id" not in c for c in out["heat"])


def test_command_center_uses_polygon_centroid():
    polygons = {
        "features": [{
            "properties": {"pin_code": "560038", "area": "Indiranagar"},
            "geometry": {"coordinates": [[[77.60, 12.96], [77.62, 12.96], [77.62, 12.98], [77.60, 12.98]]]},
        }]
    }
    out = command_center(**_fixture(), polygons=polygons, k=K)
    hub = next(h for h in out["hubs"] if h["pin_code"] == "560038")
    assert hub["location_precision"] == "polygon"
    assert hub["polygon"][0] == [12.96, 77.60]
    assert abs(hub["lat"] - 12.97) < 1e-9 and abs(hub["lon"] - 77.61) < 1e-9


def test_garment_profile_k_gate_and_brand_mix():
    items = _fixture()["garments"]
    assert garment_profile(items[:2], my_brand="", k=K)["suppressed"] is True

    p = garment_profile(items, my_brand="MyBrand", k=K)
    assert p["garments"] == 5 and p["customers"] == 5
    assert [s["size"] for s in p["size_curve"]] == ["M", "L"]
    assert p["colors"][0]["value"] == "navy"
    # brand mix only counts reviewed (completed) garments
    assert [b["brand"] for b in p["brand_mix"]] == ["Zara"]
    assert p["competitor_share"] == 1.0

    reviewed_too_few = [dict(g, review_status="needs_review") for g in items]
    q = garment_profile(reviewed_too_few, my_brand="MyBrand", k=K)
    assert q["brand_mix"] == [] and q["competitor_share"] is None


def test_haversine_km():
    assert haversine_km((12.97, 77.59), (12.97, 77.59)) == 0
    assert 1.0 < haversine_km((12.97, 77.59), (12.98, 77.59)) < 1.2


def test_research_lab_silhouettes_and_profiles():
    assert silhouette_for("Top Wear") == "tee"
    assert silhouette_for("Bottom Wear") == "pants"
    assert silhouette_for("Casual Jacket") == "hoodie"
    assert silhouette_for("Saree") == "dress"
    assert silhouette_for("Towel") == "generic"

    opps = [
        {"rank": 1, "commodity": "Top Wear", "opportunity_score": 81.5, "zones_touched": 4, "support_quantity": 90, "confidence": "high"},
        {"rank": 2, "commodity": "Towel", "opportunity_score": 40.0, "zones_touched": 2, "support_quantity": 30, "confidence": "low"},
    ]
    lab = research_lab(opps, _fixture()["garments"], my_brand="MyBrand", k=K)
    assert [i["silhouette"] for i in lab["items"]] == ["tee", "generic"]
    assert lab["items"][0]["garments"]["suppressed"] is False
    assert lab["items"][0]["garments"]["materials"][0]["value"] == "cotton"
    assert lab["items"][1]["garments"]["suppressed"] is True
    assert lab["overall"]["garments"] == 5
