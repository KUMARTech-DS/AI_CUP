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
import { PinChoropleth } from "@/components/PinChoropleth";
import { CommandCenter } from "@/components/geo/CommandCenter";
import { DemoMix } from "@/components/DemoMix";
import { DemoFilters, DemoFilterState, EMPTY_DEMO_FILTERS, demoFilterBody } from "@/components/DemoFilters";
import { API_BASE, apiGet, apiPost, Envelope } from "@/lib/api";

async function downloadGeoCsv() {
  const token = typeof window !== "undefined" ? localStorage.getItem("suffai_token") : null;
  const res = await fetch(`${API_BASE}/v1/exports/geo.csv`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "geo_aggregates.csv";
  a.click();
  URL.revokeObjectURL(url);
}

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
  demographics?: {
    cohort_customers?: number | null;
    suppressed?: boolean;
    gender_mix?: { value: string; customers?: number | null; suppressed?: boolean }[];
    age_band_mix?: { value: string; customers?: number | null; suppressed?: boolean }[];
    device_mix?: { value: string; customers?: number | null; suppressed?: boolean }[];
    note?: string;
  };
  garments?: GarmentGeo | null;
  meta: {
    mapped_active_customers: number;
    unknown_pin_customers: number;
    eligible_orders: number;
    coverage_note: string;
    privacy_k: number;
  };
};

type TopValue = { value: string; garments: number };
type OverallValue = { value: string; garments: number | null; customers: number | null; suppressed: boolean };

type GarmentGeo = {
  by_pin: {
    geo_key: string;
    pin_code: string | null;
    area: string;
    garments: number | null;
    customers: number | null;
    reviewed_garments: number | null;
    suppressed: boolean;
    top: Record<string, TopValue[]>;
  }[];
  overall: Record<string, OverallValue[]>;
  garments_total: number | null;
  customers_total: number | null;
  reviewed_garments: number | null;
  suppressed: boolean;
  privacy_k: number;
  note: string;
};

const GARMENT_ATTR_LABELS: Record<string, string> = {
  commodity_type: "Commodity",
  primary_color: "Color",
  material_type: "Material",
  pattern: "Pattern",
  gender: "Garment gender",
  brand_name_raw: "Brand",
};

function topLabel(rows?: TopValue[]): string {
  return (rows || []).map((r) => `${r.value} (${r.garments})`).join(", ") || "—";
}

type Choropleth = {
  type: string;
  disclaimer?: string;
  features: {
    type: string;
    properties: {
      pin_code?: string;
      area?: string;
      metric_value?: number | null;
      suppressed?: boolean;
    };
    geometry: { type: string; coordinates: number[][][] };
  }[];
};

export default function GeoPage() {
  const [data, setData] = useState<GeoData | null>(null);
  const [choropleth, setChoropleth] = useState<Choropleth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<DemoFilterState>(EMPTY_DEMO_FILTERS);

  function load(f: DemoFilterState = EMPTY_DEMO_FILTERS) {
    setError(null);
    apiPost<GeoData>("/v1/geo/query", {
      geo_level: "pin",
      metrics: ["active_customers"],
      ...demoFilterBody(f),
    })
      .then((res: Envelope<GeoData>) => {
        if (res.errors?.length) setError(res.errors[0].message);
        else setData(res.data);
      })
      .catch(() => setError("Failed to load geo aggregates from API"));
  }

  useEffect(() => {
    load();
    apiGet<Choropleth>("/v1/geo/choropleth")
      .then((res) => {
        if (!res.errors?.length && res.data) setChoropleth(res.data);
      })
      .catch(() => undefined);
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
      <CommandCenter />
      <div className="topbar">
        <div>
          <h1>Geo Intelligence</h1>
          <p className="sub">
            Active customers by order PIN and service commodity popularity.
            Quantities are laundry service observations — not clothing purchases.
          </p>
        </div>
        <button className="btn secondary" type="button" onClick={() => downloadGeoCsv()}>
          Export CSV
        </button>
      </div>

      <DemoFilters value={filters} onChange={setFilters} onApply={load} />

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

      {choropleth && (
        <div className="panel" style={{ marginBottom: "1rem" }}>
          <h3 style={{ marginTop: 0 }}>PIN choropleth</h3>
          <p className="sub">Active customers joined to approximate PIN polygons (k-suppressed cells muted).</p>
          <PinChoropleth features={choropleth.features} disclaimer={choropleth.disclaimer} />
        </div>
      )}

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

      <DemoMix demographics={data?.demographics} title="Customer demographics in selected zones" />

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
        <h3 style={{ marginTop: 0 }}>Garment attributes by PIN</h3>
        <p className="sub">{data?.garments?.note}</p>
        {!data?.garments || data.garments.suppressed ? (
          <div className="alert">
            No garment extractions above privacy k={data?.garments?.privacy_k ?? 30} for this filter yet.
            Images uploaded to GCS as useruuid_orderuuid_itemuuid.jpg appear here after Vertex extraction.
          </div>
        ) : (
          <>
            <div className="grid-metrics">
              <div className="metric">
                <div className="label">Extracted garments</div>
                <div className="value">{data.garments.garments_total?.toLocaleString() ?? "—"}</div>
              </div>
              <div className="metric">
                <div className="label">Customers</div>
                <div className="value">{data.garments.customers_total?.toLocaleString() ?? "—"}</div>
              </div>
              <div className="metric">
                <div className="label">Reviewed</div>
                <div className="value">{data.garments.reviewed_garments?.toLocaleString() ?? "—"}</div>
              </div>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Zone</th>
                  <th>Garments</th>
                  <th>Customers</th>
                  {Object.values(GARMENT_ATTR_LABELS).map((l) => <th key={l}>{l}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.garments.by_pin.filter((p) => !p.suppressed).slice(0, 25).map((p) => (
                  <tr key={p.geo_key}>
                    <td>{p.area}{p.pin_code ? ` (${p.pin_code})` : ""}</td>
                    <td>{p.garments}</td>
                    <td>{p.customers}</td>
                    {Object.keys(GARMENT_ATTR_LABELS).map((k) => <td key={k}>{topLabel(p.top[k])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="split-2" style={{ marginTop: "1rem" }}>
              {Object.entries(GARMENT_ATTR_LABELS).map(([k, label]) => {
                const rows = (data.garments?.overall[k] || []).filter((r) => !r.suppressed && r.value !== "unknown");
                if (!rows.length) return null;
                return (
                  <div key={k}>
                    <h4 style={{ marginBottom: 4 }}>{label}</h4>
                    <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                      {rows.slice(0, 6).map((r) => <li key={r.value}>{r.value}: {r.garments}</li>)}
                    </ul>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </RequireAuth>
  );
}
