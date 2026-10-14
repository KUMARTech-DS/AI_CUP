"""
Cloud Run Job: incremental source ingest (Mongo CDC-style or JSON snapshot refresh).

- If MONGO_URI set: pull updated docs since watermark (updated_at overlap window).
- Else: re-read DATA_DIR / optional GCS raw bucket and bump watermark, then call bq_sync.

  python -m jobs.cdc_ingest
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import settings
from jobs.bq_sync import run as bq_sync_run
from suffai.gcp_io import read_state_json, source_data_location, write_state_json
from suffai.ingest.loader import _as_list
from suffai.ids import extract_datetime, extract_id

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("suffai.cdc")


WATERMARK_NAME = "cdc_watermark.json"


def _load_watermark() -> datetime:
    data = read_state_json(WATERMARK_NAME, settings.CDC_WATERMARK_PATH)
    try:
        return datetime.fromisoformat(data["watermark"])
    except Exception:
        return datetime.now(timezone.utc) - timedelta(days=3650)


def _save_watermark(ts: datetime, stats: dict) -> None:
    write_state_json(WATERMARK_NAME, {"watermark": ts.isoformat(), "stats": stats}, settings.CDC_WATERMARK_PATH)


def _read_snapshot(location: str, filename: str) -> list:
    if location.startswith("gs://"):
        from suffai.gcp_io import read_gcs_json

        return _as_list(read_gcs_json(f"{location.rstrip('/')}/{filename}"))
    path = Path(location) / filename
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _write_snapshot(location: str, filename: str, rows: list) -> None:
    if location.startswith("gs://"):
        from suffai.gcp_io import write_gcs_json

        write_gcs_json(f"{location.rstrip('/')}/{filename}", rows)
        return
    path = Path(location) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8")


def _sync_mongo(since: datetime) -> dict:
    from pymongo import MongoClient

    client = MongoClient(settings.MONGO_URI)
    db = client[settings.MONGO_DB]
    overlap = since - timedelta(hours=2)
    location = source_data_location()
    stats = {}
    for coll in ("orders", "customers", "stores", "commodities", "services"):
        if coll not in db.list_collection_names():
            stats[coll] = 0
            continue
        query = {"updated_at": {"$gte": overlap}}
        docs = list(db[coll].find(query))
        # Merge into existing snapshot (idempotent by _id)
        existing = _read_snapshot(location, f"{coll}.json")
        by_id = {extract_id(d.get("_id")): d for d in existing if extract_id(d.get("_id"))}
        for d in docs:
            # bson → json-friendly
            d = json.loads(json.dumps(d, default=str))
            by_id[extract_id(d.get("_id"))] = d
        merged = list(by_id.values())
        _write_snapshot(location, f"{coll}.json", merged)
        stats[coll] = len(docs)
        log.info("mongo %s pulled=%s total=%s", coll, len(docs), len(merged))
    return stats


def run() -> dict:
    since = _load_watermark()
    now = datetime.now(timezone.utc)
    stats: dict = {"mode": "json_snapshot", "since": since.isoformat()}

    if settings.MONGO_URI:
        stats["mode"] = "mongo_incremental"
        stats["mongo"] = _sync_mongo(since)
    else:
        log.info("MONGO_URI unset — refreshing warehouse from %s", source_data_location())

    # Always push current curated snapshot into BigQuery when credentials available
    try:
        bq = bq_sync_run()
        stats["bigquery"] = bq
    except Exception as exc:
        log.warning("bq_sync skipped: %s", exc)
        stats["bigquery_error"] = str(exc)

    _save_watermark(now, stats)
    return stats


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
