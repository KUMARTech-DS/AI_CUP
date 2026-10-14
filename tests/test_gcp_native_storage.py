"""Cloud-backed storage paths: Cloud SQL document store, GCS source data, relational stores."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from services import docstore, ops_db


class FakeDocsDB:
    """Minimal stand-in for ops_json_documents behind services.ops_db.connect()."""

    def __init__(self):
        self.docs: dict[tuple[str, str], dict] = {}
        self.order: list[tuple[str, str]] = []
        self.statements: list[str] = []

    @contextmanager
    def connect(self):
        yield self

    def cursor(self):
        db = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=()):
                db.statements.append(sql)
                if sql.lstrip().startswith("SELECT doc_id"):
                    (collection,) = params
                    self._rows = [
                        {"doc_id": k[1], "payload": db.docs[k]} for k in db.order if k[0] == collection
                    ]
                elif sql.lstrip().startswith("DELETE"):
                    collection, ids = params
                    for did in ids:
                        db.docs.pop((collection, did), None)
                        if (collection, did) in db.order:
                            db.order.remove((collection, did))

            def executemany(self, sql, seq):
                for collection, did, payload in seq:
                    key = (collection, did)
                    if key not in db.docs:
                        db.order.append(key)
                    db.docs[key] = payload.obj

            def fetchall(self):
                return self._rows

        return Cur()


@pytest.fixture
def sql_backend(monkeypatch):
    fake = FakeDocsDB()
    monkeypatch.setattr(ops_db, "connect", fake.connect)
    monkeypatch.setattr(docstore.CloudSQLBackend, "_schema_ready", True)
    return docstore.CloudSQLBackend(), fake


def test_cloudsql_backend_roundtrip_preserves_order_and_diffs(sql_backend):
    backend, fake = sql_backend
    rows = backend.load("campaigns")
    rows += [{"campaign_id": "c1", "n": 1}, {"campaign_id": "c2", "n": 2}]
    backend.save("campaigns", rows)
    assert [r["campaign_id"] for r in backend.load("campaigns")] == ["c1", "c2"]

    rows = backend.load("campaigns")
    rows[0]["n"] = 10
    rows = [r for r in rows if r["campaign_id"] != "c2"]
    fake.statements.clear()
    backend.save("campaigns", rows)
    assert backend.load("campaigns") == [{"campaign_id": "c1", "n": 10}]
    assert any(s.lstrip().startswith("DELETE") for s in fake.statements)


def test_cloudsql_backend_does_not_delete_rows_written_by_another_instance(sql_backend):
    backend, fake = sql_backend
    mine = backend.load("audit_events")
    # Another Cloud Run instance appends after this request loaded.
    other = docstore.CloudSQLBackend()
    theirs = other.load("audit_events") + [{"id": "b"}]
    other.save("audit_events", theirs)
    backend.save("audit_events", mine + [{"id": "a"}])
    assert {r["id"] for r in backend.load("audit_events")} == {"a", "b"}


def test_doc_ids_use_collection_primary_key():
    image = {"image_id": "img-1", "garment_instance_id": "g-1"}
    assert docstore.doc_id_for("garment_images", image) == "img-1"
    assert docstore.doc_id_for("extraction_jobs", {"job_id": "j", "image_id": "i"}) == "j"
    assert docstore.doc_id_for("unknown", {"x": 1}).startswith("sha1:")


def test_garment_store_uses_cloudsql_when_database_url_set(sql_backend, monkeypatch, tmp_path):
    backend, _ = sql_backend
    monkeypatch.setattr(ops_db, "configured", lambda: True)
    monkeypatch.setattr(docstore, "_cloudsql", backend)
    from suffai.garments.store import GarmentStore

    store = GarmentStore(tmp_path)
    assert store.backend is backend
    store.create_campaign(tenant_id="t", image_object_key="k")
    assert len(store._load("campaigns")) == 1
    assert not list(tmp_path.iterdir())


def test_loader_reads_gcs_location(monkeypatch):
    seen = []

    def fake_read(uri):
        seen.append(uri)
        return [{"_id": "o1"}] if uri.endswith("/orders.json") else None

    monkeypatch.setattr("suffai.gcp_io.read_gcs_json", fake_read)
    from suffai.ingest.loader import load_source_collections

    out = load_source_collections("gs://raw-bucket/data_offline/")
    assert out["orders"] == [{"_id": "o1"}]
    assert out["customers"] == []
    assert "gs://raw-bucket/data_offline/customers.json" in seen


def test_source_location_and_state_use_gcs_when_bucket_set(monkeypatch, tmp_path):
    from suffai import gcp_io

    monkeypatch.setattr(gcp_io.settings, "GCS_RAW_BUCKET", "raw-bucket")
    monkeypatch.setattr(gcp_io.settings, "GCS_DATA_PREFIX", "data_offline/")
    assert gcp_io.source_data_location() == "gs://raw-bucket/data_offline"

    store = {}
    monkeypatch.setattr(gcp_io, "write_gcs_json", lambda uri, data: store.__setitem__(uri, data))
    monkeypatch.setattr(gcp_io, "read_gcs_json", lambda uri: store.get(uri))
    local = tmp_path / "wm.json"
    gcp_io.write_state_json("cdc_watermark.json", {"watermark": "x"}, local)
    assert store == {"gs://raw-bucket/_state/cdc_watermark.json": {"watermark": "x"}}
    assert gcp_io.read_state_json("cdc_watermark.json", local) == {"watermark": "x"}
    assert not local.exists()

    monkeypatch.setattr(gcp_io.settings, "GCS_RAW_BUCKET", "")
    assert gcp_io.source_data_location() == str(gcp_io.settings.DATA_DIR)


def test_customer_store_reads_and_writes_cloudsql(monkeypatch, tmp_path):
    written = []
    monkeypatch.setattr(ops_db, "configured", lambda: True)
    monkeypatch.setattr(ops_db, "list_customers", lambda: [{"customer_uuid": "u1", "gender": "female"}])
    monkeypatch.setattr(ops_db, "upsert_customers", lambda rows, strict=False: written.append((rows, strict)))
    from suffai.customers.store import CustomerStore

    store = CustomerStore(tmp_path)
    assert store.get("u1")["gender"] == "female"
    store.upsert({"customer_uuid": "u2", "phone_number": "999"})
    assert written[0][1] is True and [r["customer_uuid"] for r in written[0][0]] == ["u2"]
    assert not (tmp_path / "customers.json").exists()


def test_privacy_log_uses_cloudsql(monkeypatch):
    from suffai.privacy import differencing

    logged = []
    prior_ids = [f"c{i}" for i in range(40)]
    monkeypatch.setattr(ops_db, "configured", lambda: True)
    monkeypatch.setattr(
        ops_db,
        "recent_privacy_queries",
        lambda tenant_id, limit=500: [{"tenant_id": tenant_id, "cohort_size": 40, "customer_ids": prior_ids, "fingerprint": "p"}],
    )
    monkeypatch.setattr(ops_db, "mirror_privacy_query", lambda entry, risk, strict=False: logged.append((entry, strict)))
    monkeypatch.setattr(differencing.settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(differencing.settings, "PRIVACY_K", 30)
    monkeypatch.setattr(differencing.settings, "PRIVACY_DIFFERENCING_ENABLED", True)

    res = differencing.register_query(tenant_id="t", filters={"a": 1}, cohort_customer_ids=prior_ids[:35])
    assert res["differencing_risk"] is True
    assert logged and logged[0][1] is True


def test_health_reports_local_fallbacks(monkeypatch):
    from api.routers import health

    s = health.settings
    monkeypatch.setattr(s, "GCS_RAW_BUCKET", "raw")
    monkeypatch.setattr(s, "DATABASE_URL", "postgresql://x")
    monkeypatch.setattr(s, "USE_BIGQUERY_MARTS", True)
    monkeypatch.setattr(s, "GCS_IMAGE_BUCKET", "img")
    assert health.storage_report()["gcp_native"] is True
    monkeypatch.setattr(s, "DATABASE_URL", "")
    report = health.storage_report()
    assert report["gcp_native"] is False
    assert report["operational_store"] == "local_files"
