"""Collection storage for API operational records.

* Cloud SQL (`ops_json_documents`) whenever DATABASE_URL is set — the only mode safe on
  Cloud Run, where the container filesystem is per-instance and wiped on restart.
* JSON files under a directory otherwise (local dev and tests).

Both backends expose load(collection) -> list[dict] and save(collection, rows).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("suffai.docstore")

PRIMARY_KEYS: Dict[str, str] = {
    "garment_instances": "garment_instance_id",
    "garment_images": "image_id",
    "consent_records": "consent_id",
    "attribute_corrections": "correction_id",
    "extraction_jobs": "job_id",
    "brand_catalog_skus": "sku_id",
    "fashion_brands": "brand_id",
    "campaigns": "campaign_id",
    "campaign_evaluations": "evaluation_id",
    "saved_views": "view_id",
    "audit_events": "id",
}


def doc_id_for(collection: str, row: Dict[str, Any]) -> str:
    key = PRIMARY_KEYS.get(collection)
    value = row.get(key) if key else None
    value = value or row.get("id")
    if value:
        return str(value)
    payload = json.dumps(row, sort_keys=True, default=str)
    return "sha1:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()


class FileBackend:
    kind = "file"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, collection: str) -> Path:
        return self.root / f"{collection}.json"

    def load(self, collection: str) -> list[dict[str, Any]]:
        path = self._path(collection)
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, collection: str, rows: list[dict[str, Any]]) -> None:
        self._path(collection).write_text(
            json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8"
        )


class CloudSQLBackend:
    """One row per document in ops_json_documents, ordered by insertion (seq).

    save() writes only the difference against the rows this process last loaded, so
    concurrent Cloud Run instances appending to the same collection don't erase each
    other's documents.
    """

    kind = "cloudsql"
    _schema_ready = False
    _schema_lock = threading.Lock()

    def __init__(self) -> None:
        self._local = threading.local()
        self._ensure_schema()

    @property
    def _snapshots(self) -> Dict[str, Dict[str, str]]:
        if not hasattr(self._local, "snapshots"):
            self._local.snapshots = {}
        return self._local.snapshots

    @classmethod
    def _ensure_schema(cls) -> None:
        if cls._schema_ready:
            return
        with cls._schema_lock:
            if cls._schema_ready:
                return
            from services.ops_db import _run_migration_file

            _run_migration_file("002_adv_privacy_ops.sql")
            _run_migration_file("005_ops_documents_order.sql")
            cls._schema_ready = True

    def load(self, collection: str) -> list[dict[str, Any]]:
        from services.ops_db import connect

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT doc_id, payload FROM ops_json_documents WHERE collection = %s ORDER BY seq",
                    (collection,),
                )
                fetched = cur.fetchall()
        rows = [r["payload"] for r in fetched]
        self._snapshots[collection] = {
            r["doc_id"]: json.dumps(r["payload"], sort_keys=True, default=str) for r in fetched
        }
        return rows

    def save(self, collection: str, rows: list[dict[str, Any]]) -> None:
        from psycopg.types.json import Json

        from services.ops_db import connect

        before = self._snapshots.get(collection)
        after: Dict[str, str] = {}
        payloads: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            did = doc_id_for(collection, row)
            serialised = json.dumps(row, sort_keys=True, default=str)
            after[did] = serialised
            payloads[did] = json.loads(serialised)

        changed = [d for d, s in after.items() if before is None or before.get(d) != s]
        removed = [d for d in (before or {}) if d not in after]

        with connect() as conn:
            with conn.cursor() as cur:
                if removed:
                    cur.execute(
                        "DELETE FROM ops_json_documents WHERE collection = %s AND doc_id = ANY(%s)",
                        (collection, removed),
                    )
                if changed:
                    cur.executemany(
                        """
                        INSERT INTO ops_json_documents (collection, doc_id, payload, updated_at)
                        VALUES (%s, %s, %s, now())
                        ON CONFLICT (collection, doc_id)
                        DO UPDATE SET payload = EXCLUDED.payload, updated_at = now()
                        """,
                        [(collection, d, Json(payloads[d])) for d in changed],
                    )
        self._snapshots[collection] = after


_cloudsql: Optional[CloudSQLBackend] = None
_cloudsql_lock = threading.Lock()


def get_backend(file_root: str | Path) -> FileBackend | CloudSQLBackend:
    """Cloud SQL when DATABASE_URL is set; otherwise JSON files under file_root."""
    from services.ops_db import configured

    if not configured():
        return FileBackend(file_root)
    global _cloudsql
    with _cloudsql_lock:
        if _cloudsql is None:
            _cloudsql = CloudSQLBackend()
        return _cloudsql
