"""Local JSON store for new garment entities (does not modify source exports)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class GarmentStore:
    def __init__(self, derived_dir: str | Path):
        self.root = Path(derived_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in (
            "garment_instances",
            "garment_images",
            "consent_records",
            "attribute_corrections",
            "extraction_jobs",
            "brand_catalog_skus",
            "fashion_brands",
            "campaigns",
            "campaign_evaluations",
        ):
            path = self.root / f"{name}.json"
            if not path.exists():
                path.write_text("[]\n", encoding="utf-8")

    def _load(self, name: str) -> list[dict[str, Any]]:
        return json.loads((self.root / f"{name}.json").read_text(encoding="utf-8"))

    def _save(self, name: str, rows: list[dict[str, Any]]) -> None:
        (self.root / f"{name}.json").write_text(
            json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8"
        )

    def list_garment_instances(self) -> list[dict[str, Any]]:
        return self._load("garment_instances")

    def list_verified_brand_observations(self) -> list[dict[str, Any]]:
        """Only reviewed + non-null brand — used for brand analytics."""
        return [
            g for g in self.list_garment_instances()
            if g.get("review_status") == "completed"
            and g.get("brand_id")
            and not g.get("deleted_at")
        ]

    def create_consent(
        self,
        source_customer_id: str,
        purpose: str,
        *,
        policy_version: str = "v1",
    ) -> dict[str, Any]:
        rows = self._load("consent_records")
        now = datetime.now(timezone.utc).isoformat()
        rec = {
            "consent_id": str(uuid.uuid4()),
            "source_customer_id": source_customer_id,
            "purpose": purpose,
            "policy_version": policy_version,
            "status": "granted",
            "granted_at": now,
            "revoked_at": None,
            "created_at": now,
        }
        rows.append(rec)
        self._save("consent_records", rows)
        return rec

    def register_garment_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Register a physical garment only when photographic/tag evidence exists.
        Requires unique (order_id, line_id, physical_item_index).
        """
        required = [
            "source_customer_id",
            "source_order_id",
            "source_order_item_id",
            "source_order_commodity_line_id",
            "physical_item_index",
            "consent_id",
        ]
        missing = [k for k in required if not payload.get(k)]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")
        if int(payload["physical_item_index"]) < 1:
            raise ValueError("physical_item_index must be >= 1")

        rows = self.list_garment_instances()
        key = (
            payload["source_order_id"],
            payload["source_order_commodity_line_id"],
            int(payload["physical_item_index"]),
        )
        for g in rows:
            existing = (
                g.get("source_order_id"),
                g.get("source_order_commodity_line_id"),
                int(g.get("physical_item_index") or 0),
            )
            if existing == key and not g.get("deleted_at"):
                raise ValueError("Duplicate garment instance for order line + index")

        now = datetime.now(timezone.utc).isoformat()
        rec = {
            "garment_instance_id": str(uuid.uuid4()),
            "source_user_id": payload.get("source_user_id"),
            "bill_no": payload.get("bill_no"),
            "source_store_id": payload.get("source_store_id"),
            "source_service_id": payload.get("source_service_id"),
            "source_catalog_commodity_id": payload.get("source_catalog_commodity_id"),
            "order_pin_code": payload.get("order_pin_code"),
            "observed_at": payload.get("observed_at") or now,
            "commodity_type": payload.get("commodity_type"),
            "material_type": payload.get("material_type") or "unknown",
            "primary_color": payload.get("primary_color") or "unknown",
            "size_normalized": payload.get("size_normalized"),
            "brand_id": payload.get("brand_id"),
            "brand_name_raw": payload.get("brand_name_raw") or "unknown",
            "attribute_evidence": payload.get("attribute_evidence") or {},
            "review_status": payload.get("review_status") or "needs_review",
            "model_version": payload.get("model_version"),
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
            **{k: payload[k] for k in required},
        }
        rows.append(rec)
        self._save("garment_instances", rows)
        return rec

    def brand_coverage_summary(self) -> dict[str, Any]:
        all_g = self.list_garment_instances()
        verified = self.list_verified_brand_observations()
        return {
            "garment_instances_total": len(all_g),
            "verified_branded_observations": len(verified),
            "brand_analytics_available": len(verified) > 0,
            "empty_state": (
                None
                if verified
                else "No verified consented garment brand observations yet. "
                     "Brand share and competitor benchmarks remain unavailable."
            ),
        }
