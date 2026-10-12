"use client";

import { RequireAuth } from "@/components/RequireAuth";

export default function CampaignsPage() {
  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Campaign Lab</h1>
          <p className="sub">
            Upload creative → extract attributes → rank geo × demographic × RFM fit.
            Fit is relevance, not promised conversion lift.
          </p>
        </div>
      </div>
      <div className="panel">
        <div className="alert info">
          Campaign evaluate is async on Cloud Run (`POST /v1/campaigns/{"{id}"}/evaluate`).
          Images land in private GCS; scores require privacy-threshold cohorts.
        </div>
        <p className="sub">
          Wire marketer upload UI after Identity Platform roles (`brand_marketer`) and signed URL
          initiation are enabled in the target GCP project.
        </p>
      </div>
    </RequireAuth>
  );
}
