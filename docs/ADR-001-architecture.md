# ADR-001: GCP Cloud Run architecture (no Streamlit)

## Decision

Ship Suffai as GCP-native services:

1. **suffai-web** — Next.js 14 (App Router, `output: "standalone"`) on Cloud Run  
2. **suffai-api** — FastAPI BFF on Cloud Run, OpenAPI-aligned  
3. **suffai-extraction** — Pub/Sub push worker on Cloud Run  
4. **Cloud SQL PostgreSQL** — operational entities (garments, consent, tenants, campaigns)  
5. **BigQuery** — analytical marts  
6. **GCS** — raw snapshots + private garment images  
7. **Identity Platform** (target) — replace demo JWT in production  

Streamlit demos are archived under `archive/streamlit/` and are not deployed.

## Consequences

- Local development uses `uvicorn` + `next dev` or `docker compose`.  
- Terraform `enable_resources` defaults to false until project/region/billing confirmation.  
- Vertex AI calls remain behind `ENABLE_VERTEX=false` by default.
