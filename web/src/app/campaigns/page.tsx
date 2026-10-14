"use client";

import { FormEvent, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { Demographics } from "@/components/DemoMix";
import { apiPost, API_BASE, getUser } from "@/lib/api";

type EvalRow = {
  segment_key: string;
  pin_code: string;
  rfm_segment: string;
  fit_score: number | null;
  suppressed?: boolean;
  explanation?: string;
  support_count?: number;
  components?: { gender_affinity?: number; age_band_affinity?: number } | null;
  demographics?: Demographics;
};

const TARGET_GENDERS = ["unknown", "male", "female"];
const TARGET_AGE_BANDS = ["unknown", "18_24", "25_34", "35_44", "45_54", "55_plus"];

function topMix(rows?: { value: string; customers?: number | null; suppressed?: boolean }[]): string {
  const top = (rows || []).find((r) => !r.suppressed && r.value !== "unknown");
  return top ? `${top.value} (${top.customers ?? "—"})` : "—";
}

export default function CampaignsPage() {
  const user = getUser();
  const [commodity, setCommodity] = useState("shirt");
  const [color, setColor] = useState("navy");
  const [material, setMaterial] = useState("cotton");
  const [targetGender, setTargetGender] = useState("unknown");
  const [targetAge, setTargetAge] = useState("unknown");
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<EvalRow[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setStatus(null);
    const attributes = {
      commodity_type: commodity,
      primary_color: color,
      material_type: material,
      target_gender: targetGender,
      target_age_band: targetAge,
    };
    try {
      const created = await apiPost<{
        campaign: { campaign_id: string };
        upload: { mode: string; upload_url: string; method: string; gcs_object_key: string };
      }>("/v1/campaigns", {
        metadata: { brand: user?.brand },
        attributes,
      });
      if (created.errors?.length) {
        setStatus(created.errors[0].message);
        setBusy(false);
        return;
      }
      const campaignId = created.data.campaign.campaign_id;
      const upload = created.data.upload;

      if (file && upload.mode === "local") {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("gcs_object_key", upload.gcs_object_key);
        const token = typeof window !== "undefined" ? localStorage.getItem("suffai_token") : null;
        await fetch(`${API_BASE}/v1/garments/images/upload-local`, {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: fd,
        });
      }

      const ev = await apiPost<{ evaluations: EvalRow[] }>(`/v1/campaigns/${campaignId}/evaluate`, {
        attributes,
      });
      if (ev.errors?.length) {
        setStatus(ev.errors[0].message);
      } else {
        setRows(ev.data?.evaluations || []);
        setStatus(`Campaign ${campaignId} ranked ${(ev.data?.evaluations || []).length} segments.`);
      }
    } catch {
      setStatus("Campaign evaluate failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Campaign Lab</h1>
          <p className="sub">
            Upload creative (optional), set tags, rank geo × RFM fit. Scores are relevance — not conversion lift.
          </p>
        </div>
      </div>

      <form className="panel" onSubmit={onSubmit}>
        <div className="split-2">
          <div className="field">
            <label>Commodity type</label>
            <input value={commodity} onChange={(e) => setCommodity(e.target.value)} required />
          </div>
          <div className="field">
            <label>Primary color</label>
            <input value={color} onChange={(e) => setColor(e.target.value)} required />
          </div>
          <div className="field">
            <label>Material</label>
            <input value={material} onChange={(e) => setMaterial(e.target.value)} required />
          </div>
          <div className="field">
            <label>Target customer gender</label>
            <select value={targetGender} onChange={(e) => setTargetGender(e.target.value)}>
              {TARGET_GENDERS.map((g) => <option key={g} value={g}>{g === "unknown" ? "Any" : g}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Target age band</label>
            <select value={targetAge} onChange={(e) => setTargetAge(e.target.value)}>
              {TARGET_AGE_BANDS.map((a) => <option key={a} value={a}>{a === "unknown" ? "Any" : a}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Creative image (optional)</label>
            <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </div>
        </div>
        <button className="btn" type="submit" disabled={busy}>
          {busy ? "Ranking…" : "Create & evaluate"}
        </button>
        {status && <div className="alert info">{status}</div>}
      </form>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <h3 style={{ marginTop: 0 }}>Ranked segments</h3>
        {rows.length === 0 ? (
          <div className="alert">No evaluations yet.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>PIN</th>
                <th>RFM</th>
                <th>Fit</th>
                <th>Support</th>
                <th>Gender / age fit</th>
                <th>Top gender</th>
                <th>Top age band</th>
                <th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 40).map((r) => (
                <tr key={r.segment_key}>
                  <td>{r.pin_code}</td>
                  <td>{r.rfm_segment}</td>
                  <td>{r.suppressed ? "suppressed" : r.fit_score}</td>
                  <td>{r.support_count ?? "—"}</td>
                  <td>
                    {r.components
                      ? `${r.components.gender_affinity ?? "—"} / ${r.components.age_band_affinity ?? "—"}`
                      : "—"}
                  </td>
                  <td>{topMix(r.demographics?.gender_mix)}</td>
                  <td>{topMix(r.demographics?.age_band_mix)}</td>
                  <td style={{ maxWidth: 360 }}>{r.explanation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </RequireAuth>
  );
}
