"use client";

import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
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
};

export default function ResearchPage() {
  const [rows, setRows] = useState<Opp[]>([]);
  const [meta, setMeta] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiPost<{ opportunities: Opp[]; catalog_skus: number; verified_garments: number }>(
      "/v1/product-opportunities",
      {},
    ).then((res) => {
      if (res.errors?.length) {
        setError(res.errors[0].message);
        return;
      }
      setRows(res.data?.opportunities || []);
      setMeta(
        `Catalog SKUs: ${res.data?.catalog_skus ?? 0} · Verified garments: ${res.data?.verified_garments ?? 0}`,
      );
    }).catch(() => setError("Failed to load opportunities"));
  }, []);

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
      {error && <div className="alert danger">{error}</div>}
      <div className="alert info">{meta || "Loading…"}</div>
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
                  <td>{r.confidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </RequireAuth>
  );
}
