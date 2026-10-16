# Suffai page-aware chatbot setup

The shared chatbot is embedded on `web/geo.html` and `web/ads_lab.html`. It sends the current page filters and compact aggregate context to `POST /v1/chat/assistant`. The existing `POST /v1/chat/query` endpoint is unchanged.

## GCP configuration

1. Enable Vertex AI API in the project used by the existing `suffai-api` Cloud Run service.
2. Grant the Cloud Run service account the Vertex AI User role (`roles/aiplatform.user`) on that project.
3. Set these environment variables on the API Cloud Run service:
   - `ENABLE_VERTEX=true`
   - `CHAT_VERTEX_MODEL=gemini-2.5-flash` (or a Gemini model available in your Vertex AI region)
   - `VERTEX_LOCATION=asia-south1` (or your configured Vertex AI region)
   - `GOOGLE_CLOUD_PROJECT=<your-project-id>` (or use the existing `GCP_PROJECT` / `BQ_PROJECT` configuration)
4. Deploy the API with the updated `requirements.txt` so `google-genai` is installed.
5. Deploy the updated `web/` directory, including `web/chatbot.js`.

Use the existing deployment pipeline and service account; do not put service-account keys or Gemini API keys in frontend code.

## Local testing

Install dependencies in the same Python environment used by the API:

```bash
pip install -r requirements.txt
ENABLE_VERTEX=true GOOGLE_CLOUD_PROJECT=<your-project-id> VERTEX_LOCATION=asia-south1 CHAT_VERTEX_MODEL=gemini-2.5-flash uvicorn api.main:app --host 0.0.0.0 --port 8090
```

Serve `web/` using the existing frontend/command-center workflow. If the frontend runs on port `8088` and API on `8090`, `web/config.js` already points the UI at `http://127.0.0.1:8090`.

## Context and limitations

- Geo context is assembled from the live page's current filters and aggregated metrics.
- Ads Lab context uses the current audience profile, extraction attributes and the existing 0–100 segment-fit heuristic. It does not send raw image bytes to Gemini.
- The chatbot does not claim the segment-fit score predicts conversions or sales uplift.
- The API rejects context payloads larger than 30 KB. The browser sends at most 20 ranked creatives and top preference values to keep requests bounded.
- The frontend does not send customer-level rows.
- For production, protect the chat endpoint with your application's authenticated session and apply request rate limits / quota monitoring before exposing it publicly.
