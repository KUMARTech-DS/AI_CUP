"use client";

import { FormEvent, useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { DemoMix, Demographics } from "@/components/DemoMix";
import { DemoFilters, DemoFilterState, EMPTY_DEMO_FILTERS, demoFilterQuery } from "@/components/DemoFilters";
import { apiGet, apiPost, getUser } from "@/lib/api";

const MIX_LABELS: Record<string, string> = {
  commodity_type: "Commodity",
  material_type: "Material",
  primary_color: "Color",
  size_normalized: "Size",
  gender: "Garment gender",
  customer_gender: "Customer gender",
  customer_age_band: "Customer age band",
  customer_device_type: "Customer device",
};

type Bench = {
  brand: string;
  observed_garments: number | null;
  share_of_verified_pct: number | null;
  is_my_brand?: boolean;
  suppressed?: boolean;
};

export default function BrandPage() {
  const user = getUser<{ brand: string; brand_id: string }>();
  const [message, setMessage] = useState<string>("Loading…");
  const [coverage, setCoverage] = useState<Record<string, unknown> | null>(null);
  const [benches, setBenches] = useState<Bench[]>([]);
  const [mix, setMix] = useState<Record<string, { value: string; count: number }[]>>({});
  const [sku, setSku] = useState({ brand_sku: "", category: "", color: "", material: "", size: "" });
  const [skuMsg, setSkuMsg] = useState<string | null>(null);
  const [filters, setFilters] = useState<DemoFilterState>(EMPTY_DEMO_FILTERS);
  const [demographics, setDemographics] = useState<Demographics | null>(null);

  function load(f: DemoFilterState = EMPTY_DEMO_FILTERS) {
    if (!user?.brand_id) return;
    apiGet<Record<string, unknown>>(
      `/v1/brands/${user.brand_id}/benchmarks${demoFilterQuery(f)}`,
    ).then((res) => {
      const cov = (res.data as { coverage?: Record<string, unknown> })?.coverage || null;
      setCoverage(cov);
      if (res.errors?.length) {
        setMessage(res.errors[0].message);
        setBenches([]);
        setMix({});
        setDemographics(null);
      } else {
        setMessage((res.data as { definition?: string })?.definition || "Verified garment benchmarks");
        setBenches(((res.data as { benchmarks?: Bench[] }).benchmarks) || []);
        setMix(((res.data as { my_attribute_mix?: typeof mix }).my_attribute_mix) || {});
        setDemographics((res.data as { customer_demographics?: Demographics }).customer_demographics || null);
      }
    });
  }

  useEffect(() => { load(); }, [user?.brand_id]);

  async function onCatalog(e: FormEvent) {
    e.preventDefault();
    if (!user?.brand_id) return;
    const res = await apiPost(`/v1/brands/${user.brand_id}/catalog`, sku);
    setSkuMsg(res.errors?.[0]?.message || `Saved SKU ${sku.brand_sku}`);
  }

  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Brand & Competition</h1>
          <p className="sub">
            Own-brand and competitor aggregates from verified consented garment observations only.
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
          <div className="label">Needs review</div>
          <div className="value">{String(coverage?.needs_review ?? "—")}</div>
        </div>
      </div>

      <DemoFilters value={filters} onChange={setFilters} onApply={load} />

      <div className="panel" style={{ marginBottom: "1rem" }}>
        {benches.length === 0 ? (
          <div className="alert danger">{message}</div>
        ) : (
          <>
            <p className="sub">{message}</p>
            <table>
              <thead>
                <tr>
                  <th>Brand</th>
                  <th>Observed</th>
                  <th>Share %</th>
                </tr>
              </thead>
              <tbody>
                {benches.map((b) => (
                  <tr key={b.brand} style={{ fontWeight: b.is_my_brand ? 700 : 400 }}>
                    <td>{b.brand}{b.is_my_brand ? " (you)" : ""}</td>
                    <td>{b.suppressed ? "suppressed" : b.observed_garments}</td>
                    <td>{b.share_of_verified_pct ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="split-2" style={{ marginTop: "1rem" }}>
              {Object.entries(mix).filter(([, rows]) => (rows || []).length > 0).map(([key, rows]) => (
                <div key={key}>
                  <h4>{MIX_LABELS[key] || key}</h4>
                  <ul>
                    {(rows || []).slice(0, 6).map((r) => (
                      <li key={r.value}>{r.value}: {r.count}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <DemoMix demographics={demographics} title="Customers behind verified garment observations" />

      <div className="panel" style={{ marginTop: "1rem" }}>
        <h3 style={{ marginTop: 0 }}>Brand catalog SKUs</h3>
        <p className="sub">Supply-side catalog for research underserved-gap component.</p>
        <form onSubmit={onCatalog} className="split-2">
          {(["brand_sku", "category", "material", "color", "size"] as const).map((f) => (
            <div className="field" key={f}>
              <label>{f}</label>
              <input value={sku[f]} onChange={(e) => setSku({ ...sku, [f]: e.target.value })} required={f === "brand_sku"} />
            </div>
          ))}
          <button className="btn" type="submit">Save SKU</button>
        </form>
        {skuMsg && <div className="alert info">{skuMsg}</div>}
      </div>
    </RequireAuth>
  );
}
