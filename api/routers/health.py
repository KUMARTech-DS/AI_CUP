from fastapi import APIRouter

from config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": "suffai-api",
        "environment": settings.ENVIRONMENT,
        "data_dir": str(settings.DATA_DIR),
        "gcs_bucket": settings.GCS_BUCKET or None,
        "cloud_sql_configured": bool(settings.DATABASE_URL or settings.CLOUD_SQL_INSTANCE),
        "bq_project": settings.BQ_PROJECT or None,
    }


@router.get("/ready")
def ready():
    """Cloud Run readiness — data fixtures or GCS must be reachable."""
    ok = settings.DATA_DIR.exists() or bool(settings.GCS_BUCKET)
    return {"ready": ok, "data_dir_exists": settings.DATA_DIR.exists()}
