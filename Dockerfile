# Default image for `gcloud run deploy --source .` → API service.
# Prefer explicit Dockerfiles: Dockerfile.api | Dockerfile.web | Dockerfile.worker
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    ENVIRONMENT=prod \
    DERIVED_DIR=/tmp/suffai-derived

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config ./config
COPY services ./services
COPY suffai ./suffai
COPY api ./api
COPY db ./db

EXPOSE 8080
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT}
