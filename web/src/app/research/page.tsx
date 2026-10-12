"use client";

import { RequireAuth } from "@/components/RequireAuth";

export default function ResearchPage() {
  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Product Research</h1>
          <p className="sub">
            Opportunity score = demand growth + underserved gap + segment affinity + breadth + evidence.
            Underserved gap is labeled unmeasured without brand catalog / inventory.
          </p>
        </div>
      </div>
      <div className="panel">
        <div className="alert">
          No verified garment attribute coverage yet. Product opportunity ranking stays empty
          until Phase 2 brand catalog + consented observations exist. API:
          <code> POST /v1/product-opportunities</code>.
        </div>
      </div>
    </RequireAuth>
  );
}
