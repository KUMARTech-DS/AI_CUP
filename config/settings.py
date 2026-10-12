"""GCP-first settings for Cloud Run / Cloud SQL / GCS / BigQuery."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


class Settings:
    APP_NAME = "Suffai Fashion Intelligence"
    APP_VERSION = "2.0.0"
    ENVIRONMENT = os.getenv("ENVIRONMENT", "local")  # local | staging | prod

    # Cloud Run listens on PORT
    PORT = int(os.getenv("PORT", "8080"))
    WEB_ORIGIN = os.getenv("WEB_ORIGIN", "http://localhost:3000")
    API_PUBLIC_URL = os.getenv("API_PUBLIC_URL", "http://localhost:8080")

    # Source data: local fixtures or GCS snapshot
    DATA_DIR = Path(os.getenv("DATA_DIR", ROOT_DIR / "data_offline"))
    DERIVED_DIR = Path(os.getenv("DERIVED_DIR", ROOT_DIR / "data_derived"))
    IMAGE_STORE_DIR = Path(os.getenv("IMAGE_STORE_DIR", ROOT_DIR / "captured_garments"))

    # GCS
    GCS_BUCKET = os.getenv("GCS_BUCKET", "")
    GCS_RAW_BUCKET = os.getenv("GCS_RAW_BUCKET", "")
    GCS_IMAGE_BUCKET = os.getenv("GCS_IMAGE_BUCKET", "")
    GCS_DATA_PREFIX = os.getenv("GCS_DATA_PREFIX", "data_offline/")

    # Cloud SQL PostgreSQL (operational store)
    DATABASE_URL = os.getenv("DATABASE_URL", "")  # postgresql+psycopg://...
    CLOUD_SQL_INSTANCE = os.getenv("CLOUD_SQL_INSTANCE", "")  # project:region:instance

    # BigQuery
    BQ_PROJECT = os.getenv("BQ_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    BQ_LOCATION = os.getenv("BQ_LOCATION", "asia-south1")
    BQ_RAW_DATASET = os.getenv("BQ_RAW_DATASET", "suffai_raw")
    BQ_STAGING_DATASET = os.getenv("BQ_STAGING_DATASET", "suffai_staging")
    BQ_MARTS_DATASET = os.getenv("BQ_MARTS_DATASET", "suffai_marts")

    # Pub/Sub
    PUBSUB_IMAGE_TOPIC = os.getenv("PUBSUB_IMAGE_TOPIC", "garment-image-uploaded")
    PUBSUB_IMAGE_SUBSCRIPTION = os.getenv("PUBSUB_IMAGE_SUBSCRIPTION", "garment-image-uploaded-push")

    # Vertex AI
    VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "asia-south1")
    VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-2.0-flash")
    ENABLE_VERTEX = _bool("ENABLE_VERTEX", False)

    # Auth (Identity Platform / Firebase-compatible later)
    DEMO_AUTH_ENABLED = _bool("DEMO_AUTH_ENABLED", True)
    JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-change-me-use-secret-manager")
    JWT_ISSUER = os.getenv("JWT_ISSUER", "suffai-api")

    # Mongo (optional CDC source)
    MONGO_URI = os.getenv("MONGO_URI", "")
    MONGO_DB = os.getenv("MONGO_DB", "suffai-production")

    # Privacy
    PRIVACY_K = int(os.getenv("PRIVACY_K", "30"))

    GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", os.getenv("GCP_PROJECT", ""))
    GCP_REGION = os.getenv("GCP_REGION", "asia-south1")

    BRANDS = [
        "Peter England", "Arrow", "Van Heusen", "Allen Solly",
        "Raymond", "FabIndia", "Zara", "H&M", "Levi's", "Nike",
    ]


settings = Settings()
settings.IMAGE_STORE_DIR.mkdir(parents=True, exist_ok=True)
settings.DERIVED_DIR.mkdir(parents=True, exist_ok=True)
