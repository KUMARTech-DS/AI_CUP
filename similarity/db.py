"""Cloud SQL persistence for unique garments + PIN counters."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Sequence, Tuple

from similarity.settings import settings

log = logging.getLogger("similarity.db")


@contextmanager
def connect() -> Iterator[Any]:
    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    import psycopg2

    conn = psycopg2.connect(settings.DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def event_already_processed(idempotency_key: str) -> bool:
    if not idempotency_key:
        return False
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM garment_similarity_events WHERE idempotency_key = %s LIMIT 1",
                (idempotency_key,),
            )
            return cur.fetchone() is not None


def record_event(
    *,
    gcs_bucket: Optional[str],
    gcs_object_key: str,
    user_uuid: Optional[str],
    pin_code: Optional[str],
    brand_name: Optional[str],
    outcome: str,
    matched_garment_id: Optional[str] = None,
    similarity_score: Optional[float] = None,
    error_message: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO garment_similarity_events (
                  gcs_bucket, gcs_object_key, user_uuid, pin_code, brand_name,
                  outcome, matched_garment_id, similarity_score, error_message, idempotency_key
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    gcs_bucket,
                    gcs_object_key,
                    user_uuid,
                    pin_code,
                    brand_name,
                    outcome,
                    matched_garment_id,
                    similarity_score,
                    error_message,
                    idempotency_key,
                ),
            )


def load_user_embeddings(user_uuid: str) -> List[Tuple[str, List[float], Optional[str]]]:
    """Return list of (garment_id, embedding, brand_name) for the user."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ug.garment_id::text, ug.embedding, ug.brand_name
                FROM user_wardrobe uw
                JOIN unique_garments ug ON ug.garment_id = uw.garment_id
                WHERE uw.user_uuid = %s
                """,
                (user_uuid,),
            )
            rows = cur.fetchall()
    out: List[Tuple[str, List[float], Optional[str]]] = []
    for gid, emb, brand in rows:
        vec = emb if isinstance(emb, list) else json.loads(emb)
        out.append((gid, [float(x) for x in vec], brand))
    return out


def load_global_embeddings(
    *, brand_name: Optional[str], limit: int
) -> List[Tuple[str, List[float], Optional[str]]]:
    with connect() as conn:
        with conn.cursor() as cur:
            if brand_name:
                cur.execute(
                    """
                    SELECT garment_id::text, embedding, brand_name
                    FROM unique_garments
                    WHERE brand_name IS NOT NULL AND lower(brand_name) = lower(%s)
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (brand_name, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT garment_id::text, embedding, brand_name
                    FROM unique_garments
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
    out: List[Tuple[str, List[float], Optional[str]]] = []
    for gid, emb, brand in rows:
        vec = emb if isinstance(emb, list) else json.loads(emb)
        out.append((gid, [float(x) for x in vec], brand))
    return out


def create_unique_garment(
    *,
    bucket: str,
    object_key: str,
    embedding: Sequence[float],
    model: str,
    user_uuid: str,
    pin_code: str,
    brand_name: Optional[str],
) -> str:
    garment_id = str(uuid.uuid4())
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO unique_garments (
                  garment_id, representative_gcs_bucket, representative_gcs_object_key,
                  brand_name, embedding, embedding_model, embedding_dim,
                  first_user_uuid, first_pin_code, observation_count_total
                ) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,1)
                """,
                (
                    garment_id,
                    bucket,
                    object_key,
                    brand_name,
                    json.dumps(list(embedding)),
                    model,
                    len(embedding),
                    user_uuid,
                    pin_code,
                ),
            )
            cur.execute(
                """
                INSERT INTO user_wardrobe (
                  user_uuid, garment_id, first_gcs_object_key, pin_code_at_first_sight
                ) VALUES (%s,%s,%s,%s)
                """,
                (user_uuid, garment_id, object_key, pin_code),
            )
            cur.execute(
                """
                INSERT INTO garment_pin_counts (garment_id, pin_code, observation_count)
                VALUES (%s,%s,1)
                ON CONFLICT (garment_id, pin_code)
                DO UPDATE SET observation_count = garment_pin_counts.observation_count + 1,
                              updated_at = now()
                """,
                (garment_id, pin_code),
            )
    return garment_id


def link_user_to_garment(
    *,
    user_uuid: str,
    garment_id: str,
    object_key: str,
    pin_code: str,
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_wardrobe (
                  user_uuid, garment_id, first_gcs_object_key, pin_code_at_first_sight
                ) VALUES (%s,%s,%s,%s)
                ON CONFLICT (user_uuid, garment_id) DO NOTHING
                """,
                (user_uuid, garment_id, object_key, pin_code),
            )


def increment_pin_and_total(*, garment_id: str, pin_code: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO garment_pin_counts (garment_id, pin_code, observation_count)
                VALUES (%s,%s,1)
                ON CONFLICT (garment_id, pin_code)
                DO UPDATE SET observation_count = garment_pin_counts.observation_count + 1,
                              updated_at = now()
                """,
                (garment_id, pin_code),
            )
            cur.execute(
                """
                UPDATE unique_garments
                SET observation_count_total = observation_count_total + 1,
                    updated_at = now()
                WHERE garment_id = %s
                """,
                (garment_id,),
            )


def admin_summary(
    *, pin: Optional[str] = None, brand: Optional[str] = None
) -> dict[str, Any]:
    garment_clauses: List[str] = []
    garment_params: List[Any] = []
    if brand:
        garment_clauses.append("ug.brand_name IS NOT NULL AND lower(ug.brand_name) = lower(%s)")
        garment_params.append(brand)
    if pin:
        garment_clauses.append(
            "EXISTS (SELECT 1 FROM garment_pin_counts x WHERE x.garment_id = ug.garment_id AND x.pin_code = %s)"
        )
        garment_params.append(pin)
    garment_where = ("WHERE " + " AND ".join(garment_clauses)) if garment_clauses else ""

    pin_clauses: List[str] = []
    pin_params: List[Any] = []
    if pin:
        pin_clauses.append("gpc.pin_code = %s")
        pin_params.append(pin)
    if brand:
        pin_clauses.append("ug.brand_name IS NOT NULL AND lower(ug.brand_name) = lower(%s)")
        pin_params.append(brand)
    pin_where = ("WHERE " + " AND ".join(pin_clauses)) if pin_clauses else ""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) FROM unique_garments ug
                {garment_where}
                """,
                garment_params,
            )
            unique_n = int((cur.fetchone() or (0,))[0] or 0)

            if pin:
                cur.execute(
                    f"""
                    SELECT COALESCE(SUM(gpc.observation_count), 0)
                    FROM garment_pin_counts gpc
                    JOIN unique_garments ug ON ug.garment_id = gpc.garment_id
                    {pin_where}
                    """,
                    pin_params,
                )
            else:
                cur.execute(
                    f"""
                    SELECT COALESCE(SUM(ug.observation_count_total), 0)
                    FROM unique_garments ug
                    {garment_where}
                    """,
                    garment_params,
                )
            obs_n = int((cur.fetchone() or (0,))[0] or 0)

            cur.execute(
                f"""
                SELECT COUNT(DISTINCT uw.user_uuid)
                FROM user_wardrobe uw
                JOIN unique_garments ug ON ug.garment_id = uw.garment_id
                {garment_where}
                """,
                garment_params,
            )
            users_n = int((cur.fetchone() or (0,))[0] or 0)

            cur.execute(
                f"""
                SELECT gpc.pin_code,
                       COUNT(DISTINCT ug.garment_id) AS unique_garments,
                       COALESCE(SUM(gpc.observation_count), 0) AS observations
                FROM garment_pin_counts gpc
                JOIN unique_garments ug ON ug.garment_id = gpc.garment_id
                {pin_where}
                GROUP BY gpc.pin_code
                ORDER BY observations DESC
                LIMIT 50
                """,
                pin_params,
            )
            by_pin = [
                {"pin_code": r[0], "unique_garments": int(r[1]), "observations": int(r[2])}
                for r in cur.fetchall()
            ]

            cur.execute(
                f"""
                SELECT COALESCE(ug.brand_name, '(unknown)') AS brand,
                       COUNT(*) AS unique_garments,
                       COALESCE(SUM(ug.observation_count_total), 0) AS observations
                FROM unique_garments ug
                {garment_where}
                GROUP BY COALESCE(ug.brand_name, '(unknown)')
                ORDER BY unique_garments DESC
                LIMIT 50
                """,
                garment_params,
            )
            by_brand = [
                {"brand": r[0], "unique_garments": int(r[1]), "observations": int(r[2])}
                for r in cur.fetchall()
            ]

    return {
        "filters": {"pin": pin, "brand": brand},
        "kpis": {
            "unique_garments": unique_n,
            "total_observations": obs_n,
            "unique_users": users_n,
        },
        "by_pin": by_pin,
        "by_brand": by_brand,
    }


def list_unique_garments(
    *,
    pin: Optional[str] = None,
    brand: Optional[str] = None,
    user_uuid: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    clauses: List[str] = []
    params: List[Any] = []
    joins = []
    if pin:
        joins.append("JOIN garment_pin_counts gpc ON gpc.garment_id = ug.garment_id AND gpc.pin_code = %s")
        params.append(pin)
    if user_uuid:
        joins.append("JOIN user_wardrobe uw ON uw.garment_id = ug.garment_id AND uw.user_uuid = %s")
        params.append(user_uuid)
    if brand:
        clauses.append("ug.brand_name IS NOT NULL AND lower(ug.brand_name) = lower(%s)")
        params.append(brand)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    join_sql = " ".join(joins)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT ug.garment_id::text, ug.brand_name, ug.representative_gcs_object_key,
                       ug.observation_count_total, ug.created_at
                FROM unique_garments ug
                {join_sql}
                {where}
                ORDER BY ug.updated_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            items = [
                {
                    "garment_id": r[0],
                    "brand_name": r[1],
                    "gcs_object_key": r[2],
                    "observation_count_total": int(r[3]),
                    "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in cur.fetchall()
            ]
    return {"items": items, "limit": limit, "offset": offset}


def list_garments_for_pins(
    pin_codes: Sequence[str], *, limit_per_pin: int = 400
) -> dict[str, List[dict[str, Any]]]:
    """
    Read-only: unique garments observed in each PIN with embeddings + observation_count.
    Used by brand SKU compare/rank (does not write wardrobe).
    """
    pins = [str(p).strip() for p in pin_codes if str(p).strip()]
    out: dict[str, List[dict[str, Any]]] = {p: [] for p in pins}
    if not pins:
        return out

    with connect() as conn:
        with conn.cursor() as cur:
            for pin in pins:
                cur.execute(
                    """
                    SELECT ug.garment_id::text, ug.brand_name, ug.embedding,
                           gpc.observation_count
                    FROM garment_pin_counts gpc
                    JOIN unique_garments ug ON ug.garment_id = gpc.garment_id
                    WHERE gpc.pin_code = %s
                    ORDER BY gpc.observation_count DESC, ug.updated_at DESC
                    LIMIT %s
                    """,
                    (pin, limit_per_pin),
                )
                rows = []
                for r in cur.fetchall():
                    emb = r[2]
                    if isinstance(emb, str):
                        try:
                            emb = json.loads(emb)
                        except Exception:
                            emb = None
                    if not isinstance(emb, list) or not emb:
                        continue
                    rows.append(
                        {
                            "garment_id": r[0],
                            "brand_name": r[1],
                            "embedding": [float(x) for x in emb],
                            "observation_count": int(r[3] or 0),
                        }
                    )
                out[pin] = rows
    return out
