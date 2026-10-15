"""Customer profile store — customer_uuid aligns with garment user_uuid."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("suffai.customers")


def customer_index_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index by customer_uuid (and aliases) for joins with garment user_uuid."""
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        uuid = row.get("customer_uuid") or row.get("customer_id")
        if not uuid:
            continue
        rec = dict(row)
        rec["customer_uuid"] = uuid
        index[str(uuid)] = rec
        user_id = row.get("user_id")
        if user_id and str(user_id) not in index:
            index[str(user_id)] = rec
    return index


class CustomerStore:
    """Customer profiles: Cloud SQL `customers` table when DATABASE_URL is set, else a JSON file."""

    COLLECTION = "customers"

    def __init__(self, derived_dir: str | Path):
        from services.ops_db import configured

        self.root = Path(derived_dir)
        self.use_sql = configured()

    def _path(self) -> Path:
        return self.root / f"{self.COLLECTION}.json"

    def _load(self) -> list[dict[str, Any]]:
        if self.use_sql:
            from services.ops_db import list_customers

            return list_customers()
        path = self._path()
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, rows: list[dict[str, Any]]) -> None:
        if self.use_sql:
            from services.ops_db import upsert_customers

            upsert_customers(rows, strict=True)
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._path().write_text(
            json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8"
        )

    def list_customers(self) -> list[dict[str, Any]]:
        return self._load()

    def get(self, customer_uuid: str) -> Optional[dict[str, Any]]:
        for row in self._load():
            if row.get("customer_uuid") == customer_uuid or row.get("customer_id") == customer_uuid:
                return row
            if row.get("user_id") == customer_uuid:
                return row
        return None

    def upsert(self, profile: dict[str, Any]) -> dict[str, Any]:
        uuid = profile.get("customer_uuid") or profile.get("customer_id")
        if not uuid:
            raise ValueError("customer_uuid is required")
        now = datetime.now(timezone.utc).isoformat()
        rec = {
            "customer_uuid": str(uuid),
            "customer_id": str(uuid),
            "user_id": profile.get("user_id"),
            "gender": (profile.get("gender") or "unknown"),
            "age": profile.get("age"),
            "pin": profile.get("pin") or profile.get("home_pin"),
            "address": profile.get("address"),
            "phone_number": profile.get("phone_number") or profile.get("phone_no"),
            "device_type": profile.get("device_type") or "unknown",
            "customer_name": profile.get("customer_name"),
            "created_at": profile.get("created_at") or now,
            "updated_at": now,
        }
        if self.use_sql:
            self._save([rec])
            return rec
        rows = self._load()
        replaced = False
        for i, existing in enumerate(rows):
            if existing.get("customer_uuid") == rec["customer_uuid"]:
                rec["created_at"] = existing.get("created_at") or rec["created_at"]
                rows[i] = rec
                replaced = True
                break
        if not replaced:
            rows.append(rec)
        self._save(rows)
        return rec

    def sync_from_normalized(self, customers: list[dict[str, Any]]) -> int:
        """Replace derived customers from normalized offline ingest."""
        rows = []
        now = datetime.now(timezone.utc).isoformat()
        for c in customers:
            uuid = c.get("customer_uuid") or c.get("customer_id")
            if not uuid:
                continue
            rows.append({
                "customer_uuid": str(uuid),
                "customer_id": str(uuid),
                "user_id": c.get("user_id"),
                "gender": c.get("gender") or "unknown",
                "age": c.get("age"),
                "pin": c.get("pin") or c.get("home_pin"),
                "address": c.get("address"),
                "phone_number": c.get("phone_number") or c.get("phone_no"),
                "device_type": c.get("device_type") or "unknown",
                "customer_name": c.get("customer_name"),
                "created_at": c.get("created_at") or now,
                "updated_at": c.get("updated_at") or now,
            })
        self._save(rows)
        return len(rows)

    def index(self) -> dict[str, dict[str, Any]]:
        return customer_index_from_rows(self._load())

    def public_profile(self, customer_uuid: str) -> Optional[dict[str, Any]]:
        """Ops-safe view: no phone/address."""
        row = self.get(customer_uuid)
        if not row:
            return None
        return {
            "customer_uuid": row.get("customer_uuid"),
            "user_id": row.get("user_id"),
            "gender": row.get("gender"),
            "age": row.get("age"),
            "pin": row.get("pin"),
            "device_type": row.get("device_type"),
        }
