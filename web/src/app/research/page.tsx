"use client";

import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { DemoMix, Demographics } from "@/components/DemoMix";
import { DemoFilters, DemoFilterState, EMPTY_DEMO_FILTERS, demoFilterBody } from "@/components/DemoFilters";
import { ResearchLab, LabData } from "@/components/research/ResearchLab";
import { apiPost } from "@/lib/api";

type Opp = {
  rank: number;
  commodity: string;
  opportunity_score: number;
  components: Record<string, number | null>;
  confidence: string;
  note: string;
  support_quantity: number | null;
  zones_touched: number;
  demographics?: Demographics;
};

function topMix(rows?: { value: string; customers?: number | null; suppressed?: boolean }[]): string {
  const top = (rows || []).find((r) => !r.suppressed && r.value !== "unknown");
  return top ? `${top.value} (${top.customers ?? "—"})` : "—";
}

export default function ResearchPage() {
  const [rows, setRows] = useState<Opp[]>([]);
  const [meta, setMeta] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<DemoFilterState>(EMPTY_DEMO_FILTERS);
  const [demographics, setDemographics] = useState<Demographics | null>(null);
  const [lab, setLab] = useState<LabData | null>(null);

  function load(f: DemoFilterState = EMPTY_DEMO_FILTERS) {
    setError(null);
    apiPost<{
      opportunities: Opp[];
      catalog_skus: number;
      verified_garments: number;
      customer_demographics?: Demographics;
      lab?: LabData;
    }>("/v1/product-opportunities", demoFilterBody(f))
      .then((res) => {
        if (res.errors?.length) {
          setError(res.errors[0].message);
          return;
        }
        setRows(res.data?.opportunities || []);
        setDemographics(res.data?.customer_demographics || null);
        setLab(res.data?.lab || null);
        setMeta(
          `Catalog SKUs: ${res.data?.catalog_skus ?? 0} · Verified garments: ${res.data?.verified_garments ?? 0}`,
        );
      })
      .catch(() => setError("Failed to load opportunities"));
  }

  useEffect(() => { load(); }, []);

  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Product Research</h1>
          <p className="sub">
            Ranked opportunities from service demand, breadth, affinity, evidence, and catalog gap when available.
          </p>
        </div>
      </div>
      <DemoFilters value={filters} onChange={setFilters} onApply={load} />
      {error && <div className="alert danger">{error}</div>}
      <div className="alert info">{meta || "Loading…"}</div>
      <ResearchLab lab={lab} />
      <div className="panel">
        {rows.length === 0 ? (
          <div className="alert">No unsuppressed opportunities for current privacy threshold.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Rank</th>
                <th>Commodity</th>
                <th>Score</th>
                <th>Support qty</th>
                <th>Zones</th>
                <th>Top gender</th>
                <th>Top age band</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.rank}>
                  <td>{r.rank}</td>
                  <td>
                    <div>{r.commodity}</div>
                    <div className="sub" style={{ fontSize: "0.8rem" }}>{r.note}</div>
                  </td>
                  <td>{r.opportunity_score}</td>
                  <td>{r.support_quantity ?? "—"}</td>
                  <td>{r.zones_touched}</td>
                  <td>{topMix(r.demographics?.gender_mix)}</td>
                  <td>{topMix(r.demographics?.age_band_mix)}</td>
                  <td>{r.confidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <DemoMix demographics={demographics} title="Customers behind ranked demand" />
    </RequireAuth>
  );
}
