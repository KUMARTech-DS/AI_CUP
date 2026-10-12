"use client";

import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { apiGet, getUser } from "@/lib/api";

export default function BrandPage() {
  const user = getUser<{ brand: string; brand_id: string }>();
  const [message, setMessage] = useState<string>("Loading…");
  const [coverage, setCoverage] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (!user?.brand_id) return;
    apiGet<Record<string, unknown>>(`/v1/brands/${user.brand_id}/benchmarks`).then((res) => {
      if (res.errors?.length) {
        setMessage(res.errors[0].message);
        setCoverage((res.data as { coverage?: Record<string, unknown> })?.coverage || null);
      } else {
        setMessage("Verified brand observations available.");
        setCoverage((res.data as { coverage?: Record<string, unknown> })?.coverage || null);
      }
    });
  }, [user?.brand_id]);

  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Brand & Competition</h1>
          <p className="sub">
            Own-brand and competitor aggregates from verified consented garment observations only.
            Laundry stores are not fashion brands.
          </p>
        </div>
      </div>

      <div className="grid-metrics">
        <div className="metric">
          <div className="label">Tenant brand</div>
          <div className="value" style={{ fontSize: "1.2rem" }}>{user?.brand}</div>
        </div>
        <div className="metric">
          <div className="label">Garment instances</div>
          <div className="value">{String(coverage?.garment_instances_total ?? "—")}</div>
        </div>
        <div className="metric">
          <div className="label">Verified branded</div>
          <div className="value">{String(coverage?.verified_branded_observations ?? "—")}</div>
        </div>
        <div className="metric">
          <div className="label">Analytics ready</div>
          <div className="value" style={{ fontSize: "1.2rem" }}>
            {coverage?.brand_analytics_available ? "Yes" : "No"}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="alert danger">{message}</div>
        <h3>Unlock path (GCP)</h3>
        <ol>
          <li>Upload garment images to private GCS via signed URL (`POST /v1/garments/images:initiate`).</li>
          <li>Pub/Sub → Cloud Run extraction worker (Vertex Gemini, abstain-on-unknown).</li>
          <li>Human review in Cloud SQL `attribute_corrections` before B2B aggregates.</li>
          <li>Onboard `brand_catalog_skus` for supply-side context.</li>
        </ol>
      </div>
    </RequireAuth>
  );
}
