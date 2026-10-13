"use client";

import { FormEvent, useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { apiGet, apiPost, getUser } from "@/lib/api";

type InventoryRow = {
  inventory_id?: string;
  brand_id: string;
  sku: string;
  on_hand: number;
  warehouse: string;
};

type Uplift = {
  campaign_id: string;
  treatment: { n: number; conversions: number; cvr: number | null; revenue: number };
  control: { n: number; conversions: number; cvr: number | null; revenue: number };
  uplift_cvr_pp: number | null;
  method: string;
  caveat: string;
};

export default function ActivationPage() {
  const user = getUser<{ brand_id?: string; brand?: string }>();
  const [campaignId, setCampaignId] = useState("");
  const [segmentKey, setSegmentKey] = useState("Champions");
  const [revenue, setRevenue] = useState("0");
  const [sku, setSku] = useState("");
  const [onHand, setOnHand] = useState("10");
  const [inventory, setInventory] = useState<InventoryRow[]>([]);
  const [uplift, setUplift] = useState<Uplift | null>(null);
  const [schedule, setSchedule] = useState<unknown>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function refreshInventory() {
    const brandId = user?.brand_id || "";
    apiGet<{ inventory: InventoryRow[] }>(
      `/v1/activation/inventory${brandId ? `?brand_id=${encodeURIComponent(brandId)}` : ""}`
    ).then((res) => {
      if (!res.errors?.length) setInventory(res.data?.inventory || []);
    });
  }

  useEffect(() => {
    refreshInventory();
    apiGet("/v1/activation/snapshots/schedule-status").then((res) => {
      if (!res.errors?.length) setSchedule(res.data);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onConversion(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMsg(null);
    const res = await apiPost("/v1/activation/conversions", {
      campaign_id: campaignId,
      segment_key: segmentKey,
      converted: true,
      revenue: Number(revenue) || 0,
      channel: "web",
    });
    if (res.errors?.length) setError(res.errors[0].message);
    else setMsg("Conversion recorded (observational feedback).");
  }

  async function onUplift() {
    setError(null);
    const res = await apiPost<Uplift>("/v1/activation/uplift/estimate", {
      campaign_id: campaignId,
      treatment_segment_keys: [segmentKey],
      control_segment_keys: [],
    });
    if (res.errors?.length) setError(res.errors[0].message);
    else setUplift(res.data);
  }

  async function onMediaPush() {
    setError(null);
    setMsg(null);
    const res = await apiPost("/v1/activation/media/push", {
      campaign_id: campaignId,
      segment_keys: [segmentKey],
      channel: "external_dsp",
      payload: { brand: user?.brand },
    });
    if (res.errors?.length) setError(res.errors[0].message);
    else setMsg(`Media push: ${JSON.stringify(res.data?.delivery || res.data)}`);
  }

  async function onInventory(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const res = await apiPost("/v1/activation/inventory", {
      brand_id: user?.brand_id || "unknown",
      sku,
      on_hand: Number(onHand) || 0,
      warehouse: "default",
    });
    if (res.errors?.length) setError(res.errors[0].message);
    else {
      setMsg("Inventory upserted.");
      refreshInventory();
    }
  }

  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Activation &amp; uplift</h1>
          <p className="sub">
            ADV-01 — conversion feedback, observational uplift proxy, media activation webhook, inventory.
          </p>
        </div>
      </div>

      {error && <div className="alert danger">{error}</div>}
      {msg && <div className="alert info">{msg}</div>}

      <div className="split-2">
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Conversion feedback</h3>
          <form onSubmit={onConversion}>
            <div className="field">
              <label htmlFor="cid">Campaign ID</label>
              <input id="cid" value={campaignId} onChange={(e) => setCampaignId(e.target.value)} required />
            </div>
            <div className="field">
              <label htmlFor="seg">Segment key</label>
              <input id="seg" value={segmentKey} onChange={(e) => setSegmentKey(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="rev">Revenue</label>
              <input id="rev" value={revenue} onChange={(e) => setRevenue(e.target.value)} />
            </div>
            <button className="btn" type="submit">
              Record conversion
            </button>
          </form>
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
            <button className="btn secondary" type="button" onClick={onUplift} disabled={!campaignId}>
              Estimate uplift
            </button>
            <button className="btn secondary" type="button" onClick={onMediaPush} disabled={!campaignId}>
              Push to media
            </button>
          </div>
          {uplift && (
            <div className="alert info" style={{ marginTop: "1rem" }}>
              <div>
                Uplift CVR (pp): <strong>{uplift.uplift_cvr_pp ?? "—"}</strong> ({uplift.method})
              </div>
              <div style={{ marginTop: "0.5rem", fontSize: "0.9rem" }}>{uplift.caveat}</div>
            </div>
          )}
        </div>

        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Inventory</h3>
          <form onSubmit={onInventory}>
            <div className="field">
              <label htmlFor="sku">SKU</label>
              <input id="sku" value={sku} onChange={(e) => setSku(e.target.value)} required />
            </div>
            <div className="field">
              <label htmlFor="oh">On hand</label>
              <input id="oh" value={onHand} onChange={(e) => setOnHand(e.target.value)} />
            </div>
            <button className="btn" type="submit">
              Upsert inventory
            </button>
          </form>
          <table className="data-table" style={{ marginTop: "1rem", width: "100%" }}>
            <thead>
              <tr>
                <th>SKU</th>
                <th>On hand</th>
                <th>Warehouse</th>
              </tr>
            </thead>
            <tbody>
              {inventory.map((r) => (
                <tr key={`${r.brand_id}-${r.sku}-${r.warehouse}`}>
                  <td>{r.sku}</td>
                  <td>{r.on_hand}</td>
                  <td>{r.warehouse}</td>
                </tr>
              ))}
              {!inventory.length && (
                <tr>
                  <td colSpan={3} className="sub">
                    No inventory rows yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <h3 style={{ marginTop: 0 }}>EXP-01 / CDC schedule status</h3>
        <pre style={{ overflow: "auto", fontSize: "0.85rem" }}>
          {JSON.stringify(schedule, null, 2)}
        </pre>
      </div>
    </RequireAuth>
  );
}
