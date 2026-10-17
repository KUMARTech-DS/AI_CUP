"""Style Assistant picks: computed at Extract & Rank, served read-only."""

import json

import pytest

import admin_intel
import brand_campaign as bc
import style_assistant as sa

CID = "44444444-4444-4444-8444-444444444444"
CID2 = "55555555-5555-4555-8555-555555555555"


def _w(gid, name, emb, color=None):
    attrs = {"commodity": name, "name": name}
    if color:
        attrs["color"] = color
    return {"garment_id": gid, "brand_name": "Levis", "gcs_bucket": "wb", "gcs_object_key": f"w/{gid}.jpg",
            "embedding": emb, "label": name, "attributes": attrs, "commodity_type": name}


def _s(sid, name, emb, brand="Zara"):
    return {"sku_uuid": sid, "brand_name": brand, "gcs_bucket": "sb", "gcs_object_key": f"s/{sid}.jpg",
            "attributes": {"name": name, "commodity": name, "color": "black"}, "embedding": emb,
            "original_filename": f"{sid}.jpg", "extract_ok": True, "label": name}


WARDROBE = lambda: [  # noqa: E731
    _w("g-shirt", "Shirt", [1.0, 0.0, 0.0]),
    _w("g-jeans", "Jeans", [0.0, 1.0, 0.0]),
    _w("g-mystery", "Thing", [0.0, 0.0, 1.0]),
]
SKUS = lambda: [  # noqa: E731
    _s("s-shirt", "Shirt", [0.9, 0.1, 0.0]),
    _s("s-jeans", "Jeans", [0.1, 0.9, 0.0]),
    _s("s-jacket", "Jacket", [0.95, 0.0, 0.05]),
]


def test_strict_complementary_pairs():
    assert sa.categories_complementary("top", "top", strict=True) is False
    assert sa.categories_complementary("unknown", "top", strict=True) is False
    assert sa.categories_complementary("unknown", "unknown", strict=True) is False
    assert sa.categories_complementary("top", "bottom", strict=True) is True
    assert sa.categories_complementary("unknown", "top") is True


def test_complete_outfit_never_pairs_same_or_unknown_category():
    payload, fit = sa.build_customer_picks(WARDROBE(), SKUS(), "Zara")
    assert fit is not None
    looks = {look["buy"]["sku_uuid"]: look for look in payload["complete_outfit"]}
    assert looks
    for look in payload["complete_outfit"]:
        sku_cat = look["buy"]["category_bucket"]
        for own in look["own"]:
            assert own["category_bucket"] not in (sku_cat, "unknown")
    assert {o["garment_id"] for o in looks["s-shirt"]["own"]} == {"g-jeans"}
    assert "g-shirt" in {o["garment_id"] for o in looks["s-jeans"]["own"]}


def test_recommended_allows_any_category():
    payload, _ = sa.build_customer_picks(WARDROBE(), SKUS(), "Zara")
    rec = {p["buy"]["sku_uuid"]: p for p in payload["recommended"]}
    assert rec["s-jacket"]["liked"]["garment_id"] == "g-shirt"
    assert rec["s-jacket"]["liked"]["category_bucket"] != rec["s-jacket"]["buy"]["category_bucket"]
    assert rec["s-shirt"]["liked"]["garment_id"] == "g-shirt"
    assert rec["s-jacket"]["title"].startswith("Since you have")


def test_stored_payload_has_no_signed_urls_or_identity():
    payload, _ = sa.build_customer_picks(WARDROBE(), SKUS(), "Zara")
    blob = json.dumps(payload)
    assert "image_url" not in blob and "user_uuid" not in blob
    assert set(payload) == {"brand_name", "complete_outfit", "recommended", "wardrobe"}
    assert len(payload["wardrobe"]) == 3
    assert payload["complete_outfit"][0]["buy"]["gcs_object_key"].startswith("s/")


def test_cosine_lookup_matches_python():
    cos = sa._cosine_lookup(SKUS(), WARDROBE())
    assert cos[("s-shirt", "g-shirt")] == pytest.approx(sa.cosine_similarity([0.9, 0.1, 0.0], [1.0, 0.0, 0.0]))
    assert len(cos) == 9


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


CUSTOMERS = {
    "u1": {"age_group": "18-24", "gender": "Female", "rfm_segment": "Champions"},
    "u2": {"age_group": "18-24", "gender": "M", "rfm_segment": "Champions"},
    "u3": {"age_group": "35-44", "gender": "F", "rfm_segment": "Champions"},
}


@pytest.fixture
def pin_users(monkeypatch):
    monkeypatch.setattr(bc, "eligible_user_uuids_for_campaign", lambda c, cap=None: ["u1", "u2", "u3"])


def test_eligible_narrowed_by_age_gender_rfm(pin_users, monkeypatch):
    monkeypatch.setattr(admin_intel, "dataset", lambda **kw: {"customers": CUSTOMERS})
    camp = {"pin_codes": ["560008"], "age_group": "18-24", "gender": "F", "rfm_segment": "champions"}
    assert sa.eligible_customers(camp) == ["u1"]
    assert sa.eligible_customers({**camp, "gender": None}) == ["u1", "u2"]
    assert sa.eligible_customers({"pin_codes": ["560008"]}) == ["u1", "u2", "u3"]


def test_eligible_falls_back_to_pin_only(pin_users, monkeypatch):
    camp = {"pin_codes": ["560008"], "age_group": "18-24"}

    def down(**kw):
        raise admin_intel.IntelUnavailable("down")

    monkeypatch.setattr(admin_intel, "dataset", down)
    assert sa.eligible_customers(camp) == ["u1", "u2", "u3"]
    monkeypatch.setattr(admin_intel, "dataset", lambda **kw: {"customers": {"other": CUSTOMERS["u1"]}})
    assert sa.eligible_customers(camp) == ["u1", "u2", "u3"]


def test_personalise_campaign_saves_picks(pin_users, monkeypatch):
    saved, impact = [], []
    monkeypatch.setattr(bc, "get_campaign", lambda cid: {"campaign_id": cid, "brand_name": "Zara", "pin_codes": ["560008"]})
    monkeypatch.setattr(sa, "list_campaign_skus_rich", lambda cid, limit=80: SKUS())
    monkeypatch.setattr(sa, "load_user_wardrobe_rich", lambda u, limit=80: WARDROBE() if u != "u3" else [])
    monkeypatch.setattr(bc, "upsert_user_recommendation", lambda **kw: saved.append(kw))
    monkeypatch.setattr(bc, "set_campaign_impact", lambda cid, **kw: impact.append(kw))
    out = sa.personalise_campaign(CID)
    assert out["eligible_users"] == 3 and out["recommendations_computed"] == 2
    assert [s["user_uuid"] for s in saved] == ["u1", "u2"]
    assert saved[0]["payload"]["complete_outfit"] and saved[0]["payload"]["recommended"]
    assert impact == [{"eligible_users": 3}, {"recommendations_computed": 2}]


# ---------------------------------------------------------------------------
# Read-only feed
# ---------------------------------------------------------------------------


def _rows():
    zara, _ = sa.build_customer_picks(WARDROBE(), SKUS(), "Zara")
    hm, _ = sa.build_customer_picks(WARDROBE(), [_s("h-jeans", "Jeans", [0.0, 1.0, 0.0], brand="H&M")], "H&M")
    zara_again = json.loads(json.dumps(zara))
    for look in zara_again["complete_outfit"]:
        look["score"] = 1.0
    return [
        {"campaign_id": CID, "brand_name": "Zara", "fit_score": 80.0, "payload": zara},
        {"campaign_id": CID2, "brand_name": "H&M", "fit_score": 70.0, "payload": hm},
        {"campaign_id": "66666666-6666-4666-8666-666666666666", "brand_name": "Zara", "fit_score": 10.0, "payload": zara_again},
    ]


@pytest.fixture
def feed_env(monkeypatch):
    served = []
    monkeypatch.setattr(bc, "list_recs_for_user", lambda u, limit=50: _rows())
    monkeypatch.setattr(sa, "load_user_wardrobe_rich", lambda u, limit=80: WARDROBE())
    monkeypatch.setattr(sa, "signed_image_url", lambda b, k, **kw: f"https://signed/{k}" if k else None)
    monkeypatch.setattr(bc, "set_campaign_impact", lambda cid, **kw: served.append(cid))
    return served


def test_feed_merges_dedupes_and_signs(feed_env):
    out = sa.customer_style_feed("u1")
    assert out["brands"] == ["H&M", "Zara"]
    skus = [look["buy"]["sku_uuid"] for look in out["complete_outfit"]]
    assert len(skus) == len(set(skus)) and "h-jeans" in skus
    assert all(look["score"] != 1.0 for look in out["complete_outfit"])
    assert [look["rank"] for look in out["complete_outfit"]] == list(range(1, len(skus) + 1))
    assert all(look["buy"]["image_url"].startswith("https://signed/s/") for look in out["complete_outfit"])
    assert all(o["image_url"].startswith("https://signed/w/") for look in out["complete_outfit"] for o in look["own"])
    assert all(p["liked"]["image_url"] for p in out["recommended"])
    assert len(out["wardrobe"]) == 3 and out["wardrobe"][0]["image_url"]
    blob = json.dumps(out)
    assert CID not in blob and CID2 not in blob and "campaign_id" not in blob


def test_feed_brand_filter(feed_env):
    out = sa.customer_style_feed("u1", brand="h&m")
    assert {look["buy"]["brand_name"] for look in out["complete_outfit"] + out["recommended"]} == {"H&M"}
    assert out["brands"] == ["H&M", "Zara"]
    assert feed_env == [CID2]


def test_feed_status_set_excludes_discarded():
    assert set(bc.STYLE_FEED_STATUSES) == {"ranked", "configured", "launched", "exported", "started"}
    assert "ignored" not in bc.STYLE_FEED_STATUSES and "draft" not in bc.STYLE_FEED_STATUSES


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def test_recommend_route_removed_and_feed_forced_to_own_customer(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    seen = {}
    monkeypatch.setattr(main, "_require_session", lambda *a, **k: {"role": "customer", "customer_id": "u1", "token": "t"})
    monkeypatch.setattr(main.style_asst, "customer_style_feed", lambda u, brand=None, limit=24: seen.update(u=u, brand=brand) or {"ok": True})
    client = TestClient(main.app)
    assert client.post("/v1/style-assistant/recommend", json={"user_uuid": "u2", "brand_name": "Zara"}).status_code in (404, 405)
    assert client.get("/v1/style-assistant/brands").status_code == 404
    r = client.get("/v1/style-assistant/feed", params={"user_uuid": "u2", "brand": "Zara"})
    assert r.status_code == 200 and seen == {"u": "u1", "brand": "Zara"}

    def boom(*a, **k):
        raise RuntimeError("psycopg2 password=secret")

    monkeypatch.setattr(main.style_asst, "customer_style_feed", boom)
    r = client.get("/v1/style-assistant/feed")
    assert r.status_code == 503 and "psycopg2" not in r.text
