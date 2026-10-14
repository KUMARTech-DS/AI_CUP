from fastapi import APIRouter

from config import settings
from suffai.gcp_io import source_data_location

router = APIRouter(tags=["health"])


def storage_report() -> dict:
    """Where each kind of data lives; `local_fallbacks` must be empty on Cloud Run."""
    report = {
        "source_data": source_data_location(),
        "operational_store": "cloudsql" if settings.DATABASE_URL else "local_files",
        "dashboard_store": "bigquery" if settings.USE_BIGQUERY_MARTS else "source_json",
        "image_bucket": settings.GCS_IMAGE_BUCKET or settings.GCS_BUCKET or None,
        "job_state": f"gs://{settings.GCS_RAW_BUCKET}/_state" if settings.GCS_RAW_BUCKET else "local_files",
    }
    fallbacks = []
    if not settings.GCS_RAW_BUCKET:
        fallbacks.append("GCS_RAW_BUCKET unset: source JSON and job state read from container disk")
    if not settings.DATABASE_URL:
        fallbacks.append("DATABASE_URL unset: garments, campaigns, customers, privacy log on container disk")
    if not settings.USE_BIGQUERY_MARTS:
        fallbacks.append("USE_BIGQUERY_MARTS=false: dashboard not reading BigQuery")
    if not report["image_bucket"]:
        fallbacks.append("GCS_IMAGE_BUCKET unset: uploads stored on container disk")
    report["local_fallbacks"] = fallbacks
    report["gcp_native"] = not fallbacks
    return report


@router.get("/health")
def health():
    sql = {"configured": bool(settings.DATABASE_URL or settings.CLOUD_SQL_INSTANCE)}
    try:
        from services.ops_db import health as sql_health

        sql = sql_health()
    except Exception as exc:
        sql = {**sql, "error": str(exc)}
    return {
        "status": "ok",
        "service": "suffai-api",
        "environment": settings.ENVIRONMENT,
        "storage": storage_report(),
        "gcs_bucket": settings.GCS_BUCKET or None,
        "cloud_sql": sql,
        "cloud_sql_configured": bool(settings.DATABASE_URL or settings.CLOUD_SQL_INSTANCE),
        "bq_project": settings.BQ_PROJECT or None,
        "use_bigquery_marts": settings.USE_BIGQUERY_MARTS,
        "identity_platform_enabled": settings.IDENTITY_PLATFORM_ENABLED,
        "privacy_differencing": settings.PRIVACY_DIFFERENCING_ENABLED,
        "adv_enabled": settings.ENABLE_ADV,
        "sku_ranking": {
            "vertex_enabled": settings.ENABLE_VERTEX,
            "vertex_model": settings.VERTEX_MODEL if settings.ENABLE_VERTEX else None,
            "consent_mode": settings.SKU_RANK_CONSENT_MODE,
        },
    }


@router.get("/ready")
def ready():
    """Cloud Run readiness — source data must be reachable (GCS in the cloud, DATA_DIR locally)."""
    ok = bool(settings.GCS_RAW_BUCKET) or settings.DATA_DIR.exists()
    return {"ready": ok, "source_data": source_data_location()}
