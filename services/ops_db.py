"""Cloud SQL dual-write for API operational records (GCP-native persistence)."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from config import settings

log = logging.getLogger("suffai.ops_db")


def configured() -> bool:
    return bool(settings.DATABASE_URL)


@contextmanager
def connect() -> Iterator[Any]:
    import psycopg
    from psycopg.rows import dict_row

    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg.connect(settings.DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _run_migration_file(filename: str) -> bool:
    from suffai.sql_migrations import migration_statements

    statements = migration_statements(filename)
    if not statements:
        return False
    with connect() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
    return True


def ensure_adv_schema() -> bool:
    """Idempotent create of ADV/privacy/ops mirror tables (002 migration)."""
    if not configured():
        return False
    try:
        ok = _run_migration_file("002_adv_privacy_ops.sql")
        _run_migration_file("005_ops_documents_order.sql")
        return ok
    except Exception as exc:
        log.warning("ensure_adv_schema skipped: %s", exc)
        return False


def ensure_customer_schema() -> bool:
    """Idempotent create of customers table (004 migration)."""
    if not configured():
        return False
    try:
        ok = _run_migration_file("004_customers.sql")
        try:
            _run_migration_file("003_garment_gender.sql")
        except Exception as exc:
            log.warning("003_garment_gender.sql skipped: %s", exc)
        return ok
    except Exception as exc:
        log.warning("ensure_customer_schema skipped: %s", exc)
        return False


def upsert_customers(rows: list[Dict[str, Any]], *, strict: bool = False) -> int:
    """Upsert customer profiles into Cloud SQL customers table."""
    if not configured() or not rows:
        return 0
    n = 0
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    uuid = row.get("customer_uuid") or row.get("customer_id")
                    if not uuid:
                        continue
                    cur.execute(
                        """
                        INSERT INTO customers (
                          customer_uuid, user_id, gender, age, pin, address,
                          phone_number, device_type, customer_name, created_at, updated_at
                        ) VALUES (
                          %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          COALESCE(%s::timestamptz, now()), now()
                        )
                        ON CONFLICT (customer_uuid) DO UPDATE SET
                          user_id = EXCLUDED.user_id,
                          gender = EXCLUDED.gender,
                          age = EXCLUDED.age,
                          pin = EXCLUDED.pin,
                          address = EXCLUDED.address,
                          phone_number = EXCLUDED.phone_number,
                          device_type = EXCLUDED.device_type,
                          customer_name = EXCLUDED.customer_name,
                          updated_at = now()
                        """,
                        (
                            str(uuid),
                            row.get("user_id"),
                            row.get("gender"),
                            row.get("age"),
                            row.get("pin") or row.get("home_pin"),
                            row.get("address"),
                            row.get("phone_number") or row.get("phone_no"),
                            row.get("device_type"),
                            row.get("customer_name"),
                            row.get("created_at"),
                        ),
                    )
                    n += 1
        return n
    except Exception as exc:
        if strict:
            raise
        log.warning("upsert_customers failed: %s", exc)
        return 0


def upsert_json_document(collection: str, doc_id: str, payload: Dict[str, Any]) -> bool:
    if not configured() or not doc_id:
        return False
    try:
        from psycopg.types.json import Json

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ops_json_documents (collection, doc_id, payload, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (collection, doc_id)
                    DO UPDATE SET payload = EXCLUDED.payload, updated_at = now()
                    """,
                    (collection, str(doc_id), Json(payload)),
                )
        return True
    except Exception as exc:
        log.debug("ops_json upsert failed (%s/%s): %s", collection, doc_id, exc)
        return False


def mirror_collection(collection: str, rows: list[Dict[str, Any]], id_keys: tuple[str, ...] = ("id",)) -> int:
    n = 0
    for row in rows:
        doc_id = None
        for k in id_keys:
            if row.get(k):
                doc_id = str(row[k])
                break
        if not doc_id:
            continue
        if upsert_json_document(collection, doc_id, row):
            n += 1
    return n


def insert_conversion(rec: Dict[str, Any], *, strict: bool = False) -> bool:
    if not configured():
        return False
    try:
        from psycopg.types.json import Json

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversion_events (
                      event_id, tenant_id, brand, campaign_id, segment_key,
                      converted, revenue, channel, observed_at, payload
                    ) VALUES (
                      %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::timestamptz, %s
                    )
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (
                        rec.get("event_id"),
                        rec.get("tenant_id"),
                        rec.get("brand"),
                        rec.get("campaign_id"),
                        rec.get("segment_key"),
                        bool(rec.get("converted", True)),
                        float(rec.get("revenue") or 0),
                        rec.get("channel"),
                        rec.get("observed_at"),
                        Json(rec),
                    ),
                )
        return True
    except Exception as exc:
        if strict:
            raise
        log.warning("conversion insert failed: %s", exc)
        return False


def upsert_inventory(rec: Dict[str, Any], *, strict: bool = False) -> bool:
    if not configured():
        return False
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO inventory_levels (
                      inventory_id, brand_id, sku, warehouse, on_hand, as_of, updated_at
                    ) VALUES (
                      COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s::timestamptz, now()
                    )
                    ON CONFLICT (brand_id, sku, warehouse)
                    DO UPDATE SET on_hand = EXCLUDED.on_hand, as_of = EXCLUDED.as_of, updated_at = now()
                    """,
                    (
                        rec.get("inventory_id"),
                        rec.get("brand_id"),
                        rec.get("sku"),
                        rec.get("warehouse") or "default",
                        int(rec.get("on_hand") or 0),
                        rec.get("as_of") or rec.get("updated_at"),
                    ),
                )
        return True
    except Exception as exc:
        if strict:
            raise
        log.warning("inventory upsert failed: %s", exc)
        return False


def insert_media_activation(envelope: Dict[str, Any], delivery: Dict[str, Any], *, strict: bool = False) -> bool:
    if not configured():
        return False
    try:
        from psycopg.types.json import Json

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO media_activation_log (
                      request_id, tenant_id, brand, campaign_id, channel,
                      segment_keys, payload, delivery
                    ) VALUES (
                      %s::uuid, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (request_id) DO NOTHING
                    """,
                    (
                        envelope.get("request_id"),
                        envelope.get("tenant_id"),
                        envelope.get("brand"),
                        envelope.get("campaign_id"),
                        envelope.get("channel"),
                        Json(envelope.get("segment_keys") or []),
                        Json(envelope.get("payload") or {}),
                        Json(delivery),
                    ),
                )
        return True
    except Exception as exc:
        if strict:
            raise
        log.warning("media activation insert failed: %s", exc)
        return False


def mirror_privacy_query(entry: Dict[str, Any], risk: Dict[str, Any], *, strict: bool = False) -> bool:
    if not configured():
        return False
    try:
        from psycopg.types.json import Json

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO privacy_query_log (
                      fingerprint, tenant_id, filters, cohort_size, cohort_hash,
                      customer_ids, differencing_risk, risk_reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry.get("fingerprint"),
                        entry.get("tenant_id"),
                        Json(entry.get("filters") or {}),
                        entry.get("cohort_size"),
                        entry.get("cohort_hash"),
                        Json(entry.get("customer_ids") or []),
                        bool(risk.get("differencing_risk")),
                        risk.get("reason"),
                    ),
                )
        return True
    except Exception as exc:
        if strict:
            raise
        log.debug("privacy log mirror failed: %s", exc)
        return False


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def list_customers() -> list[Dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT customer_uuid, user_id, gender, age, pin, address, phone_number,
                       device_type, customer_name, created_at, updated_at
                FROM customers
                """
            )
            rows = cur.fetchall()
    return [
        {**r, "customer_id": r["customer_uuid"], "created_at": _iso(r["created_at"]),
         "updated_at": _iso(r["updated_at"])}
        for r in rows
    ]


def list_conversions(tenant_id: Optional[str], campaign_id: str) -> list[Dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload FROM conversion_events
                WHERE campaign_id = %s AND tenant_id IS NOT DISTINCT FROM %s
                ORDER BY created_at
                """,
                (campaign_id, tenant_id),
            )
            return [r["payload"] for r in cur.fetchall()]


def list_inventory(brand_id: Optional[str] = None) -> list[Dict[str, Any]]:
    sql = """
        SELECT inventory_id::text AS inventory_id, brand_id, sku, warehouse, on_hand, as_of, updated_at
        FROM inventory_levels
    """
    params: tuple = ()
    if brand_id:
        sql += " WHERE brand_id = %s"
        params = (brand_id,)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql + " ORDER BY brand_id, sku, warehouse", params)
            rows = cur.fetchall()
    return [{**r, "as_of": _iso(r["as_of"]), "updated_at": _iso(r["updated_at"])} for r in rows]


def recent_privacy_queries(tenant_id: str, limit: int = 500) -> list[Dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT fingerprint, tenant_id, filters, cohort_size, cohort_hash, customer_ids, created_at
                FROM privacy_query_log
                WHERE tenant_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (tenant_id, limit),
            )
            rows = cur.fetchall()
    return [
        {**{k: v for k, v in r.items() if k != "created_at"}, "ts": _iso(r["created_at"])}
        for r in reversed(rows)
    ]


def health() -> Dict[str, Any]:
    if not configured():
        return {"configured": False, "reachable": False}
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                cur.fetchone()
        return {"configured": True, "reachable": True, "instance": settings.CLOUD_SQL_INSTANCE or None}
    except Exception as exc:
        return {"configured": True, "reachable": False, "error": str(exc)}
