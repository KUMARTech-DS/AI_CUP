"""GCS upload → worker → BigQuery row → dashboard garment shape (no GCP calls)."""

import base64
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from suffai.garments.bq_schema import enriched_view_sql, garment_id_for, garment_row_from_extraction
from suffai.metrics.garment_geo import garment_pin_summary
from suffai.sql_migrations import split_sql_statements
from suffai.warehouse.repository import garment_from_view_row
from worker.events import storage_event_to_payload

U, O, I = (
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222",
    "33333333-3333-3333-3333-333333333333",
)
NAME = f"{U}_{O}_{I}.jpg"


def test_storage_finalize_event_becomes_payload():
    payload, reason = storage_event_to_payload(
        {"name": NAME, "bucket": "b", "contentType": "image/jpeg", "generation": "7"},
        {"eventType": "OBJECT_FINALIZE"},
    )
    assert reason == "ok"
    assert payload["gcs_object_key"] == NAME
    assert payload["idempotency_key"] == f"gcs:b/{NAME}#7"
    assert (payload["user_uuid"], payload["order_uuid"], payload["item_uuid"]) == (U, O, I)


def test_storage_events_ignored_when_not_applicable():
    assert storage_event_to_payload({"name": NAME, "bucket": "b"}, {"eventType": "OBJECT_DELETE"})[0] is None
    assert storage_event_to_payload({"name": "campaigns/x.jpg", "bucket": "b"}, {})[0] is None
    assert storage_event_to_payload(
        {"name": NAME, "bucket": "b", "contentType": "application/pdf"}, {}
    )[0] is None


def test_garment_row_is_bigquery_safe():
    row = garment_row_from_extraction(
        observation_id="obs-1",
        result={
            "commodity_type": "shirt",
            "gender": "male",
            "brand_name": "YELLOW",
            "material_blend": {"cotton": 0.6},
            "secondary_colors": ["white", None],
            "attribute_evidence": {"gender": {"confidence": 0.9}},
            "review_status": "NEEDS_REVIEW",
        },
        status="NEEDS_REVIEW",
        user_uuid=U,
        order_uuid=O,
        item_uuid=I,
        gcs_uri=f"gs://b/{NAME}",
    )
    assert row["garment_id"] == garment_id_for(U, O, I)
    assert row["review_status"] == "needs_review"
    assert row["gender"] == "male"
    assert json.loads(row["material_blend"]) == {"cotton": 0.6}
    assert row["secondary_colors"] == ["white"]
    json.dumps(row)  # streaming insert payload must be JSON-serializable


def test_view_row_maps_to_dashboard_garment():
    g = garment_from_view_row({
        "garment_id": "gid",
        "user_uuid": U,
        "order_uuid": O,
        "item_uuid": I,
        "pin_code": "560102",
        "brand_name": "YELLOW",
        "gender": "male",
        "review_status": "COMPLETED",
        "attribute_evidence": '{"gender": {"confidence": 0.9}}',
        "customer_gender": "female",
        "customer_age_band": "25_34",
        "ingested_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
    })
    assert g["source_customer_id"] == U
    assert g["order_pin_code"] == "560102"
    assert g["brand_name_raw"] == "YELLOW"
    assert g["review_status"] == "completed"
    assert g["attribute_evidence"]["gender"]["confidence"] == 0.9
    assert g["customer_gender"] == "female"


def test_enriched_view_attributes_pin_and_skips_failures():
    sql = enriched_view_sql("p", "staging", "marts")
    assert "COALESCE(o.order_pin, c.pin) AS pin_code" in sql
    assert "!= 'FAILED'" in sql


def test_garment_pin_summary_suppresses_small_cohorts():
    big = [
        {"source_customer_id": f"c{i}", "order_pin_code": "560102", "commodity_type": "shirt",
         "gender": "male", "review_status": "completed", "customer_gender": "female"}
        for i in range(35)
    ]
    small = [
        {"source_customer_id": f"s{i}", "order_pin_code": "560034", "commodity_type": "kurta",
         "review_status": "needs_review"}
        for i in range(3)
    ]
    out = garment_pin_summary(big + small, k=30)
    pins = {p["geo_key"]: p for p in out["by_pin"]}
    assert pins["560102"]["garments"] == 35
    assert pins["560102"]["top"]["commodity_type"][0]["value"] == "shirt"
    assert pins["560034"]["suppressed"] is True and pins["560034"]["garments"] is None

    female = garment_pin_summary(big + small, genders=["female"], k=30)
    assert female["garments_total"] == 35


def test_migration_split_keeps_statement_after_header_comment():
    stmts = split_sql_statements("-- header\nALTER TABLE t ADD COLUMN x TEXT;\n-- c\nSELECT 1;")
    assert stmts == ["ALTER TABLE t ADD COLUMN x TEXT", "SELECT 1"]


def test_worker_gcs_notification_writes_bigquery_row(monkeypatch):
    import worker.main as wm
    from worker.gcs_io import GcsImage

    captured = []
    monkeypatch.setattr(wm.bq_write, "enabled", lambda: True)
    monkeypatch.setattr(wm.bq_write, "insert_garment_row", lambda row: captured.append(row) or {
        "table": "t", "observation_id": row["observation_id"], "garment_id": row["garment_id"],
    })
    monkeypatch.setattr(wm, "read_image", lambda key, bucket=None: GcsImage(
        bucket=bucket, object_key=key, gs_uri=f"gs://{bucket}/{key}",
        content_type="image/jpeg", data=b"x", size_bytes=1,
    ))
    monkeypatch.setattr(wm.settings, "ENABLE_VERTEX", False)
    monkeypatch.setattr(wm.settings, "DATABASE_URL", "")

    obj = {"name": NAME, "bucket": "garments-bkt", "contentType": "image/jpeg", "generation": "1"}
    envelope = {
        "message": {
            "data": base64.b64encode(json.dumps(obj).encode()).decode(),
            "attributes": {"eventType": "OBJECT_FINALIZE", "bucketId": "garments-bkt", "objectId": NAME},
            "messageId": "m1",
        }
    }
    resp = TestClient(wm.app).post("/", json=envelope).json()

    assert resp["user_uuid"] == U and resp["order_uuid"] == O and resp["item_uuid"] == I
    assert resp["gcs_uri"] == f"gs://garments-bkt/{NAME}"
    assert len(captured) == 1
    row = captured[0]
    assert row["garment_id"] == garment_id_for(U, O, I)
    assert row["gcs_uri"] == f"gs://garments-bkt/{NAME}"
    assert row["source"] == "gcs_event"
    assert "gender" in row

    ignored = TestClient(wm.app).post("/", json={
        "message": {
            "data": base64.b64encode(json.dumps({**obj, "name": "campaigns/c.jpg"}).encode()).decode(),
            "attributes": {"eventType": "OBJECT_FINALIZE"},
            "messageId": "m2",
        }
    }).json()
    assert ignored["status"] == "ignored"
    assert len(captured) == 1
