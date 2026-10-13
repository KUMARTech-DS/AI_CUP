"use client";

import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { apiGet, apiPatch } from "@/lib/api";

type Garment = {
  garment_instance_id: string;
  source_order_id: string;
  order_pin_code?: string;
  commodity_type?: string;
  material_type?: string;
  primary_color?: string;
  size_normalized?: string;
  brand_name_raw?: string;
  review_status?: string;
};

export default function ReviewPage() {
  const [rows, setRows] = useState<Garment[]>([]);
  const [msg, setMsg] = useState<string | null>(null);

  async function load() {
    const all = await apiGet<{ garments: Garment[] }>("/v1/garments");
    const list = (all.data?.garments || []).filter(
      (g) => (g.review_status || "") === "needs_review",
    );
    setRows(list);
  }

  useEffect(() => { load(); }, []);

  async function approve(g: Garment) {
    const body = await apiPatch(`/v1/garments/${g.garment_instance_id}/attributes`, {
      attributes: {
        brand_name_raw: g.brand_name_raw || "unknown",
        commodity_type: g.commodity_type || "unknown",
        material_type: g.material_type || "unknown",
        primary_color: g.primary_color || "unknown",
      },
      review_status: "completed",
    });
    setMsg(body.errors?.[0]?.message || `Marked ${g.garment_instance_id.slice(0, 8)} completed`);
    load();
  }

  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Garment review</h1>
          <p className="sub">Human correction before B2B brand aggregates (IMG-02 / DAT-01).</p>
        </div>
      </div>
      {msg && <div className="alert info">{msg}</div>}
      <div className="panel">
        {rows.length === 0 ? (
          <div className="alert">No garments in needs_review. Use Capture to register order photos first.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Order</th>
                <th>PIN</th>
                <th>Type</th>
                <th>Material</th>
                <th>Color</th>
                <th>Brand</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((g) => (
                <tr key={g.garment_instance_id}>
                  <td>{g.source_order_id?.slice(0, 8)}</td>
                  <td>{g.order_pin_code || "—"}</td>
                  <td>{g.commodity_type}</td>
                  <td>{g.material_type}</td>
                  <td>{g.primary_color}</td>
                  <td>{g.brand_name_raw}</td>
                  <td>
                    <button className="btn" type="button" onClick={() => approve(g)}>
                      Mark completed
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </RequireAuth>
  );
}
