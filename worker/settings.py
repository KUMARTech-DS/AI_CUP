"""Worker-only settings from env (does not change shared API config module)."""

from __future__ import annotations

import os


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


class WorkerSettings:
    PORT = int(os.getenv("PORT", "8080"))
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
    GCS_IMAGE_BUCKET = os.getenv("GCS_IMAGE_BUCKET") or os.getenv("GCS_BUCKET") or ""
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    ENABLE_VERTEX = _bool("ENABLE_VERTEX", False)
    VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "asia-south1")
    VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-2.0-flash")
    # Auto-complete when min field confidence >= this; else NEEDS_REVIEW
    AUTO_ACCEPT_CONFIDENCE = float(os.getenv("AUTO_ACCEPT_CONFIDENCE", "0.90"))
    MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(12 * 1024 * 1024)))


settings = WorkerSettings()
