"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";
import { RequireAuth } from "@/components/RequireAuth";
import { apiPost, Envelope } from "@/lib/api";

type GeoData = {
  tiles: {
    area: string;
    pin_code: string | null;
    active_customers: number | null;
    suppressed: boolean;
    geo_key: string;
  }[];
  commodities: {
    commodity_name: string;
    quantity: number | null;
    suppressed: boolean;
  }[];
  rfm_mix: { rfm_segment: string; customers: number | null; suppressed: boolean }[];
  meta: {
    mapped_active_customers: number;
    unknown_pin_customers: number;
    eligible_orders: number;
    coverage_note: string;
    privacy_k: number;
  };
};

export default function GeoPage() {
  const [data, setData] = useState<GeoData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiPost<GeoData>("/v1/geo/query", { geo_level: "pin", metrics: ["active_customers"] })
      .then((res: Envelope<GeoData>) => {
        if (res.errors?.length) setError(res.errors[0].message);
        else setData(res.data);
      })
      .catch(() => setError("Failed to load geo aggregates from API"));
  }, []);

  const tiles = (data?.tiles || [])
    .filter((t) => !t.suppressed && t.geo_key !== "UNKNOWN" && t.active_customers != null)
    .slice(0, 15);
  const commodities = (data?.commodities || [])
    .filter((c) => !c.suppressed && c.quantity != null)
    .slice(0, 12);
  const rfm = (data?.rfm_mix || []).filter((r) => !r.suppressed && r.customers != null);

  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Geo Intelligence</h1>
          <p className="sub">
            Active customers by order PIN and service commodity popularity.
            Quantities are laundry service observations — not clothing purchases.
          </p>
        </div>
      </div>

      {error && <div className="alert danger">{error}</div>}
      {data && <div className="alert info">{data.meta.coverage_note}</div>}

      <div className="grid-metrics">
        <div className="metric">
          <div className="label">Mapped customers</div>
          <div className="value">{data?.meta.mapped_active_customers?.toLocaleString() ?? "—"}</div>
        </div>
        <div className="metric">
          <div className="label">Unknown PIN</div>
          <div className="value">{data?.meta.unknown_pin_customers?.toLocaleString() ?? "—"}</div>
        </div>
        <div className="metric">
          <div className="label">Eligible orders</div>
          <div className="value">{data?.meta.eligible_orders?.toLocaleString() ?? "—"}</div>
        </div>
        <div className="metric">
          <div className="label">Privacy k</div>
          <div className="value">{data?.meta.privacy_k ?? 30}</div>
        </div>
      </div>

      <div className="split-2">
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Active customers by zone</h3>
          <div style={{ width: "100%", height: 360 }}>
            <ResponsiveContainer>
              <BarChart data={tiles} margin={{ left: 8, right: 8, top: 8, bottom: 48 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="area" angle={-28} textAnchor="end" interval={0} height={70} tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="active_customers" fill="#0f6b4c" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>RFM mix</h3>
          <div style={{ width: "100%", height: 360 }}>
            <ResponsiveContainer>
              <BarChart data={rfm}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="rfm_segment" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="customers" fill="#c45c26" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <h3 style={{ marginTop: 0 }}>Service commodity popularity</h3>
        <p className="sub">Sum of ordered quantities on eligible orders.</p>
        <div style={{ width: "100%", height: 380 }}>
          <ResponsiveContainer>
            <BarChart data={commodities} layout="vertical" margin={{ left: 24, right: 16 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="commodity_name" width={160} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="quantity" fill="#16352a" radius={[0, 6, 6, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <h3 style={{ marginTop: 0 }}>Photo-verified attributes</h3>
        <div className="alert">
          Material / color / size / brand maps populate after consented garment image
          extraction on GCS + Vertex (Cloud Run worker). Empty until IMG pipeline data exists.
        </div>
      </div>
    </RequireAuth>
  );
}
