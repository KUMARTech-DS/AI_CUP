# Default image for `gcloud run deploy --source .` → Command Center (replaces retired suffai-api).
# Prefer explicit Dockerfiles:
#   Dockerfile.command_center | Dockerfile.worker | Dockerfile.similarity
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    USE_BQ=true

WORKDIR /app

COPY command_center/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY command_center ./command_center
COPY command_center_data ./command_center_data
COPY web ./web

ENV PYTHONPATH=/app

EXPOSE 8080
CMD exec uvicorn command_center.main:app --host 0.0.0.0 --port ${PORT}
