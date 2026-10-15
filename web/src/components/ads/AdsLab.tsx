"use client";

import "./ads-lab.css";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiUpload, getUser } from "@/lib/api";
import { titleCase } from "@/lib/garmentColors";

type FactorName = "visual_relevance" | "local_adoption" | "trend_momentum" | "seasonal_relevance";
type Confidence = "high" | "medium" | "low" | "insufficient_evidence";
type Segment = { age_band: string; gender: string; customers: number; share: number };
type TopItem = { value: string; count: number; share: number | null };

type RankResult = {
  input_index: number;
  image_id: string;
  image_sha256: string;
  filename: string;
  duplicate_of: number | null;
  rank: number;
  opportunity_score: number | null;
  factor_scores: Record<FactorName, number | null>;
  factor_weights: Record<FactorName, number>;
  factor_contributions: Record<FactorName, number | null>;
  confidence: Confidence;
  evidence: {
    eligible_unique_customers: number | null;
    observation_count: number | null;
    sample_size: number | null;
    evidence_scope: string | null;
    evidence_scope_label: string;
    trend_windows: { recent: (string | null)[] | null; prior: (string | null)[] | null };
    target: {
      segments: Segment[];
      rfm_segment: { segment: string; customers: number; share: number } | null;
      suppressed: boolean;
    };
    data_as_of: string | null;
  };
  justification: string;
  missing_signals: { factor: string; reason: string }[];
  limitations: string[];
  attributes: {
    commodity_type: string;
    family: string;
    primary_color: string;
    pattern: string;
    gender: string;
    material_type: string;
    source: string | null;
    model_version: string | null;
  };
};

type AreaProfile = {
  pincode: string;
  area: string;
  garments: {
    suppressed: boolean;
    customers: number | null;
    colors?: TopItem[];
    size_curve?: { size: string; count: number; share: number }[];
    patterns?: TopItem[];
    commodity_types?: TopItem[];
  };
  service_commodities: { value: string; customers: number; share: number }[];
  apparel_customers: number | null;
  activity_change: number | null;
};

type RankRun = {
  run_id: string;
  pincode: string;
  area: string;
  lookback_days: number;
  data_as_of: string | null;
  scoring_version: string;
  privacy_k: number;
  image_count: number;
  attribute_source: string | string[];
  ranked_results: RankResult[];
  area_profile: AreaProfile;
  disclaimer: string;
};

type Area = { pincode: string; area: string; active_customers: number };
type Asset = { id: string; file: File; url: string };

const MAX_IMAGES = 20;
const MAX_AREAS = 3;
const ALLOWED = ["image/jpeg", "image/png", "image/webp"];
const MAX_SIDE = 1600;
const MAX_UPLOAD_BYTES = 1_500_000;
const FACTORS: { key: FactorName; label: string; short: string }[] = [
  { key: "visual_relevance", label: "Visual relevance", short: "VIS" },
  { key: "local_adoption", label: "Local adoption", short: "ADO" },
  { key: "trend_momentum", label: "Trend momentum", short: "TRD" },
  { key: "seasonal_relevance", label: "Seasonal relevance", short: "SEA" },
];

const ICONS: Record<string, string> = {
  rank: "M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM7 6H4a3 3 0 0 0 3 4M17 6h3a3 3 0 0 1-3 4",
  upload: "M7 18a5 5 0 1 1 .9-9.9A6 6 0 0 1 19 9a4 4 0 0 1 0 8h-1M12 12v9M8.5 15.5 12 12l3.5 3.5",
  check: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM8 12.5l2.5 2.5L16 9.5",
  radar: "M12 12 19 5M21 12a9 9 0 1 1-9-9M17 12a5 5 0 1 1-5-5M12 12h.01",
  layers: "m12 3 9 5-9 5-9-5 9-5zM3 13l9 5 9-5",
  table: "M3 5h18v14H3zM3 10h18M3 15h18M9 5v14",
  csv: "M14 3H6v18h12V7l-4-4zM14 3v4h4M9 13h6M9 17h6",
  trend: "m3 17 6-6 4 4 8-8M15 7h6v6",
  palette: "M12 3a9 9 0 0 0 0 18c1 0 1.5-.7 1.5-1.5 0-1.2-1-1.5-1-2.5 0-.8.7-1.5 1.5-1.5H16a5 5 0 0 0 5-5c0-4.1-4-7.5-9-7.5zM7.5 11h.01M10 7h.01M15 7.5h.01",
  ruler: "M3 17 17 3l4 4L7 21l-4-4zM7 13l2 2M10 10l2 2M13 7l2 2",
  shapes: "M3 3h8v8H3zM17 7a4 4 0 1 1 0 .01M7 14l4 7H3l4-7zM14 14h7v7h-7z",
  tag: "M3 12V3h9l9 9-9 9-9-9zM7.5 7.5h.01",
  crosshair: "M12 2v4M12 18v4M2 12h4M18 12h4M12 19a7 7 0 1 0 0-14 7 7 0 0 0 0 14z",
  cubes: "m12 2 8 4.5v9L12 20l-8-4.5v-9L12 2zM12 11l8-4.5M12 11 4 6.5M12 11v9",
  shirt: "M8 3 3 6l2 4 2-1v12h10V9l2 1 2-4-5-3a4 4 0 0 1-8 0z",
};

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg className="ads-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={ICONS[name]} />
    </svg>
  );
}

function ageLabel(band: string): string {
  if (band === "under_18") return "<18";
  if (band === "55_plus") return "55+";
  return band.replace("_", "-");
}

function genderLabel(g: string): string {
  return g === "unknown" ? "All" : titleCase(g);
}

function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function day(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "—";
}

function rankClass(rank: number, n: number): string {
  if (rank <= 3) return `r${rank}`;
  return n >= 4 && rank === n ? "r5" : "r4";
}

function trigger(r: RankResult, area: string): string {
  const seg = r.evidence.target.segments[0];
  const rfm = r.evidence.target.rfm_segment?.segment;
  const parts = seg ? [area, ageLabel(seg.age_band), genderLabel(seg.gender)] : ["Broad", "All ages", "All"];
  return [...parts, rfm || "—"].join(" | ");
}

function topList(items: TopItem[] | undefined, n = 5): string {
  const rows = (items || []).slice(0, n);
  return rows.length ? rows.map((x, i) => `${i + 1}. ${titleCase(x.value)}`).join(" ") : `below k`;
}

async function prepareUpload(file: File): Promise<{ blob: Blob; name: string }> {
  if (file.size <= MAX_UPLOAD_BYTES) return { blob: file, name: file.name };
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) return { blob: file, name: file.name };
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.86));
  return { blob: blob || file, name: file.name.replace(/\.[^.]+$/, "") + ".jpg" };
}

function errorMessage(status: number, message: string | undefined): string {
  if (status === 401) return "Session expired — sign in again.";
  if (status === 413) return `Images too large: ${message || "reduce size or count"}`;
  if (status === 415) return `Unsupported file type: ${message || "use JPEG, PNG or WebP"}`;
  if (status === 503) return "Ranking upstream (Vertex AI / BigQuery) is unavailable — retry shortly.";
  return message || `Ranking failed (HTTP ${status})`;
}

type MatrixRow = {
  area: string;
  age: string;
  gender: string;
  rfm: string;
  creative: string;
  imageId: string;
  score: number;
  confidence: Confidence;
};

function matrixRows(runs: RankRun[]): MatrixRow[] {
  const rows: MatrixRow[] = [];
  for (const run of runs) {
    const scored = run.ranked_results.filter((r) => r.opportunity_score !== null);
    const best = new Map<string, { seg: Segment; r: RankResult }>();
    for (const r of scored) {
      for (const seg of r.evidence.target.segments) {
        const key = `${seg.age_band}|${seg.gender}`;
        const prev = best.get(key);
        if (!prev || (prev.r.opportunity_score ?? 0) < (r.opportunity_score ?? 0)) best.set(key, { seg, r });
      }
    }
    const add = (r: RankResult, age: string, gender: string) =>
      rows.push({
        area: run.area,
        age,
        gender,
        rfm: r.evidence.target.rfm_segment?.segment || "—",
        creative: r.filename,
        imageId: r.image_id,
        score: r.opportunity_score ?? 0,
        confidence: r.confidence,
      });
    if (best.size) best.forEach(({ seg, r }) => add(r, ageLabel(seg.age_band), genderLabel(seg.gender)));
    else if (scored[0]) add(scored[0], "All ages", "All");
  }
  return rows.sort((a, b) => b.score - a.score);
}

function downloadCsv(rows: MatrixRow[]) {
  const esc = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
  const header = ["Geo Area", "Age Bucket", "Gender", "RFM Segment", "Top Creative", "Image ID", "Opportunity Score", "Confidence"];
  const lines = rows.map((r) => [r.area, r.age, r.gender, r.rfm, r.creative, r.imageId, r.score, r.confidence].map(esc).join(","));
  const blob = new Blob([[header.join(","), ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "Spatial_Creative_Matrix.csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function AdsLab() {
  const [areas, setAreas] = useState<Area[]>([]);
  const [selectedPins, setSelectedPins] = useState<string[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [lookback, setLookback] = useState(180);
  const [dragover, setDragover] = useState(false);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, RankRun>>({});
  const [activePin, setActivePin] = useState<string | null>(null);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const assetsRef = useRef<Asset[]>([]);
  const brand = getUser()?.brand;

  useEffect(() => () => assetsRef.current.forEach((a) => URL.revokeObjectURL(a.url)), []);

  useEffect(() => {
    apiGet<{ areas: Area[] }>("/api/v1/sku/areas")
      .then((res) => {
        const rows = res.data?.areas || [];
        setAreas(rows);
        setSelectedPins(rows.slice(0, 2).map((a) => a.pincode));
      })
      .catch(() => setError("Could not load target areas"));
  }, []);

  const addFiles = useCallback((list: FileList | File[]) => {
    const files = Array.from(list);
    const valid = files.filter((f) => ALLOWED.includes(f.type));
    const room = Math.max(0, MAX_IMAGES - assetsRef.current.length);
    const next = valid.slice(0, room).map((file) => ({
      id: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`,
      file,
      url: URL.createObjectURL(file),
    }));
    const dropped = files.length - next.length;
    setError(dropped > 0 ? `${dropped} file(s) skipped — JPEG/PNG/WebP only, max ${MAX_IMAGES} images.` : null);
    assetsRef.current = [...assetsRef.current, ...next];
    setAssets(assetsRef.current);
    setRuns({});
  }, []);

  function removeAsset(id: string) {
    const gone = assetsRef.current.find((a) => a.id === id);
    if (gone) URL.revokeObjectURL(gone.url);
    assetsRef.current = assetsRef.current.filter((a) => a.id !== id);
    setAssets(assetsRef.current);
    setRuns({});
  }

  function clearAssets() {
    assetsRef.current.forEach((a) => URL.revokeObjectURL(a.url));
    assetsRef.current = [];
    setAssets([]);
    setRuns({});
  }

  function togglePin(pin: string) {
    setSelectedPins((prev) =>
      prev.includes(pin) ? prev.filter((p) => p !== pin) : prev.length >= MAX_AREAS ? prev : [...prev, pin],
    );
  }

  async function runRanker() {
    if (!assets.length) {
      setError("Upload creative images first.");
      return;
    }
    if (!selectedPins.length) {
      setError("Select at least one target area.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const prepared = await Promise.all(assets.map((a) => prepareUpload(a.file)));
      const out: Record<string, RankRun> = {};
      for (const [i, pin] of selectedPins.entries()) {
        setProgress(`Area ${i + 1}/${selectedPins.length} · PIN ${pin}`);
        const fd = new FormData();
        prepared.forEach((p) => fd.append("images", p.blob, p.name));
        fd.append("pincode", pin);
        fd.append("lookback_days", String(lookback));
        const { status, body } = await apiUpload<RankRun>("/api/v1/sku/rank", fd);
        if (status !== 200 || body.errors?.length) throw new Error(errorMessage(status, body.errors?.[0]?.message));
        out[pin] = body.data;
      }
      setRuns(out);
      const first = out[selectedPins[0]];
      setActivePin(selectedPins[0]);
      setSelectedIdx(first?.ranked_results[0]?.input_index ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ranking failed");
    } finally {
      setLoading(false);
      setProgress("");
    }
  }

  const runList = useMemo(() => Object.values(runs), [runs]);
  const run = activePin ? runs[activePin] : undefined;
  const results = run?.ranked_results || [];
  const selected = results.find((r) => r.input_index === selectedIdx) || results[0];
  const matrix = useMemo(() => matrixRows(runList), [runList]);
  const profile = run?.area_profile;
  const sizes = [...(profile?.garments.size_curve || [])].sort((a, b) => b.share - a.share).slice(0, 5);
  const commodities = profile?.garments.suppressed
    ? (profile?.service_commodities || []).map((c, i) => `${i + 1}. ${c.value}`).join(" ") || "below k"
    : topList(profile?.garments.commodity_types, 3);
  const change = profile?.activity_change;

  return (
    <div className="ads-root">
      <header className="ads-header">
        <div className="ads-brand">
          <div className="ads-logo"><div><Icon name="rank" /></div></div>
          <div>
            <h1 className="ads-title">PRO ADS LAB <span className="ads-badge">Creative Optimizer</span></h1>
            <p className="ads-subtitle">SKU Ranking · PIN garment history × adoption × trend × season</p>
          </div>
        </div>
        <div className="ads-meta">
          {brand && <span className="ads-chip">Tenant <b>{brand}</b></span>}
          {run && <span className="ads-chip">Model <b>{run.scoring_version}</b></span>}
          {run && <span className="ads-chip">Data as of <b>{day(run.data_as_of)}</b></span>}
          {run && <span className="ads-chip">Privacy <b>k={run.privacy_k}</b></span>}
          {run && (
            <span className="ads-chip">
              Vision <b>{Array.isArray(run.attribute_source) ? run.attribute_source.join("+") : run.attribute_source}</b>
            </span>
          )}
        </div>
      </header>

      <div className="ads-body">
        <aside className="ads-side">
          <div className="ads-step">
            <h2 className="ads-step-title">
              <span className="ads-step-num violet">1</span>
              <span>Target Areas</span>
              <span className="ads-step-hint">up to {MAX_AREAS}</span>
            </h2>
            <div className="ads-areas">
              {areas.length === 0 && <span className="ads-step-hint">Loading areas…</span>}
              {areas.map((a) => {
                const checked = selectedPins.includes(a.pincode);
                const disabled = !checked && selectedPins.length >= MAX_AREAS;
                return (
                  <label key={a.pincode} className={`ads-area${disabled ? " disabled" : ""}`}>
                    <input
                      type="checkbox"
                      className="ads-check"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => togglePin(a.pincode)}
                    />
                    <span>{a.area === a.pincode ? a.pincode : `${a.area} (${a.pincode})`}</span>
                    <small>{a.active_customers.toLocaleString()}</small>
                  </label>
                );
              })}
            </div>
          </div>

          <div className="ads-step">
            <h2 className="ads-step-title">
              <span className="ads-step-num cyan">2</span>
              <span>Upload Creatives</span>
              <span className="ads-step-hint">{assets.length}/{MAX_IMAGES}</span>
            </h2>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept={ALLOWED.join(",")}
              hidden
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <div
              className={`ads-dropzone${dragover ? " dragover" : ""}${assets.length ? " loaded" : ""}`}
              onClick={() => fileInput.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragover(true);
              }}
              onDragLeave={() => setDragover(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragover(false);
                addFiles(e.dataTransfer.files);
              }}
            >
              <span className="big"><Icon name={assets.length ? "check" : "upload"} /></span>
              <strong>{assets.length ? `${assets.length} Assets Loaded` : "Click or Drop Images"}</strong>
              <span>{assets.length ? "Click to add more SKU variants" : "1–20 SKU images · JPEG, PNG, WebP"}</span>
            </div>
            {assets.length > 0 && (
              <>
                <div className="ads-thumbs">
                  {assets.map((a) => (
                    <div key={a.id} className="ads-thumb" title={a.file.name}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={a.url} alt={a.file.name} />
                      <button type="button" aria-label={`Remove ${a.file.name}`} onClick={() => removeAsset(a.id)}>×</button>
                    </div>
                  ))}
                </div>
                <button type="button" className="ads-link" onClick={clearAssets}>Clear all</button>
              </>
            )}
          </div>

          <div className="ads-step">
            <h2 className="ads-step-title">
              <span className="ads-step-num emerald">3</span>
              <span>History Window</span>
            </h2>
            <select className="ads-select" value={lookback} onChange={(e) => setLookback(Number(e.target.value))}>
              <option value={90}>Last 90 days</option>
              <option value={180}>Last 180 days (default)</option>
              <option value={365}>Last 365 days</option>
            </select>
          </div>

          <button type="button" className="ads-run" onClick={runRanker} disabled={loading}>
            <Icon name="radar" />
            <span>{loading ? "Ranking…" : "Execute AI Ranker"}</span>
          </button>
        </aside>

        <section className="ads-stage">
          {loading && (
            <div className="ads-overlay">
              <span className="ads-spin"><Icon name="crosshair" /></span>
              <h3>Scoring SKUs Against PIN History</h3>
              <p>Extracting garment attributes and matching consented local demand… {progress}</p>
            </div>
          )}
          {error && <div className="ads-error">{error}</div>}
          {!loading && !run && (
            <div className="ads-empty">
              <span className="big"><Icon name="cubes" /></span>
              <p>Awaiting visual asset injection.</p>
            </div>
          )}

          {run && !loading && (
            <div className="ads-results">
              <div className="ads-section">
                <div className="ads-section-head">
                  <div className="left">
                    <h3><Icon name="layers" /><span>Spatial Creative Ranking:</span></h3>
                    <select
                      className="ads-select"
                      value={activePin || ""}
                      onChange={(e) => {
                        setActivePin(e.target.value);
                        setSelectedIdx(runs[e.target.value]?.ranked_results[0]?.input_index ?? null);
                      }}
                    >
                      {runList.map((r) => (
                        <option key={r.pincode} value={r.pincode}>{r.area} ({r.pincode})</option>
                      ))}
                    </select>
                  </div>
                  <span className="note">{run.image_count} SKUs · {run.lookback_days}-day window · run {run.run_id.slice(0, 8)}</span>
                </div>

                <div className="ads-insights ads-hide-scroll">
                  <div
                    className={`ads-insight lift${change !== null && change !== undefined && change < 0 ? " down" : ""}`}
                    title="Change in distinct apparel customers, recent vs prior half of the window"
                  >
                    <Icon name="trend" />
                    <span className="label">Area Activity:</span>
                    <span className="value">
                      {change === null || change === undefined ? "below k" : `${change >= 0 ? "+" : ""}${(change * 100).toFixed(1)}%`}
                    </span>
                  </div>
                  <div className="ads-sep" />
                  <div className="ads-insight">
                    <Icon name="palette" />
                    <span className="label">Colors:</span>
                    <span className="value">{topList(profile?.garments.colors)}</span>
                  </div>
                  <div className="ads-sep" />
                  <div className="ads-insight">
                    <Icon name="ruler" />
                    <span className="label">Sizes:</span>
                    <span className="value">{sizes.length ? sizes.map((s) => `${s.size} (${pct(s.share)})`).join(", ") : "below k"}</span>
                  </div>
                  <div className="ads-sep" />
                  <div className="ads-insight">
                    <Icon name="shapes" />
                    <span className="label">Patterns:</span>
                    <span className="value">{topList(profile?.garments.patterns)}</span>
                  </div>
                  <div className="ads-sep" />
                  <div className="ads-insight">
                    <Icon name="tag" />
                    <span className="label">Commodity:</span>
                    <span className="value">{commodities}</span>
                  </div>
                </div>

                <div className="ads-flow ads-hide-scroll">
                  {results.map((r) => {
                    const asset = assets[r.input_index];
                    return (
                      <button
                        type="button"
                        key={r.input_index}
                        className={`ads-card ${rankClass(r.rank, results.length)}${selected?.input_index === r.input_index ? " selected" : ""}`}
                        onClick={() => setSelectedIdx(r.input_index)}
                      >
                        <div className="ads-card-img">
                          {asset ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img src={asset.url} alt={r.filename} />
                          ) : (
                            <span style={{ fontSize: "3.5rem", color: "#4b5563" }}><Icon name="shirt" /></span>
                          )}
                          <div className="scan" />
                          <div className="tint" />
                          <div className="score">
                            {r.opportunity_score === null ? "n/a" : r.opportunity_score.toFixed(1)}
                            <small>/100</small>
                          </div>
                        </div>
                        <div className="ads-card-body">
                          <div className="ads-card-top">
                            <span className="ads-rank">{r.rank === 1 && <Icon name="rank" />}RANK #{r.rank}</span>
                            <span className="ads-file" title={r.image_id}>{r.filename}</span>
                          </div>
                          <p className="ads-k">Target Segment Trigger</p>
                          <span className="ads-pill">{trigger(r, run.area)}</span>
                          <p className="ads-k" style={{ display: "flex", justifyContent: "space-between" }}>
                            <span>AI Reasoning</span>
                            <span className={`ads-conf ${r.confidence}`}>{r.confidence.replace("_", " ")}</span>
                          </p>
                          <p className="ads-reason">{r.justification}</p>
                          <div className="ads-bars">
                            {FACTORS.map((f) => {
                              const v = r.factor_scores[f.key];
                              return (
                                <div key={f.key} className={`ads-bar${v === null ? " missing" : ""}`} title={f.label}>
                                  <span><em>{f.short}</em><em>{v === null ? "—" : Math.round(v)}</em></span>
                                  <i><b style={{ width: `${v ?? 0}%` }} /></i>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              {selected && (
                <div className="ads-detail">
                  <div className="ads-panel">
                    <h4><Icon name="radar" />Factor Breakdown · Rank #{selected.rank} · {selected.filename}</h4>
                    {FACTORS.map((f) => {
                      const v = selected.factor_scores[f.key];
                      const c = selected.factor_contributions[f.key];
                      return (
                        <div key={f.key} className={`ads-factor${v === null ? " missing" : ""}`}>
                          <span className="name">{f.label} <small>({Math.round((selected.factor_weights[f.key] || 0) * 100)}%)</small></span>
                          <span className="track"><b style={{ width: `${v ?? 0}%` }} /></span>
                          <span className="num">{v === null ? "missing" : v.toFixed(1)}</span>
                          <span className="contrib">{c === null ? "—" : `+${c.toFixed(1)}`}</span>
                        </div>
                      );
                    })}
                    <div className="ads-total">
                      <span>Opportunity score</span>
                      <b>{selected.opportunity_score === null ? "insufficient evidence" : `${selected.opportunity_score.toFixed(1)} / 100`}</b>
                    </div>
                    <p className="ads-full-reason" style={{ marginTop: "0.9rem" }}>{selected.justification}</p>
                    {selected.missing_signals.length > 0 && (
                      <ul className="ads-notes warn">
                        {selected.missing_signals.map((m) => (
                          <li key={m.factor}>{titleCase(m.factor)} excluded — {m.reason}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div className="ads-panel">
                    <h4><Icon name="table" />Evidence</h4>
                    <dl className="ads-kv">
                      <dt>Scope</dt>
                      <dd>{selected.evidence.evidence_scope_label}</dd>
                      <dt>Comparable customers</dt>
                      <dd>{selected.evidence.eligible_unique_customers?.toLocaleString() ?? `suppressed (<k=${run.privacy_k})`}</dd>
                      <dt>Observations</dt>
                      <dd>{selected.evidence.observation_count?.toLocaleString() ?? "—"}</dd>
                      <dt>Sample size</dt>
                      <dd>{selected.evidence.sample_size?.toLocaleString() ?? "—"} eligible customers</dd>
                      <dt>Trend windows</dt>
                      <dd>
                        {selected.evidence.trend_windows.prior
                          ? `${day(selected.evidence.trend_windows.prior[0])}→${day(selected.evidence.trend_windows.prior[1])} vs ${day(selected.evidence.trend_windows.recent?.[0])}→${day(selected.evidence.trend_windows.recent?.[1])}`
                          : "—"}
                      </dd>
                      <dt>Detected</dt>
                      <dd>
                        {[selected.attributes.commodity_type, selected.attributes.primary_color, selected.attributes.pattern, selected.attributes.gender, selected.attributes.material_type]
                          .filter((v) => v && v !== "unknown")
                          .map(titleCase)
                          .join(" · ") || "No attributes detected"}{" "}
                        <small style={{ color: "#6b7280" }}>({selected.attributes.source || "—"})</small>
                      </dd>
                      <dt>Image ID</dt>
                      <dd className="ads-mono">
                        {selected.image_id}
                        {selected.duplicate_of !== null && ` · duplicate of #${selected.duplicate_of + 1}`}
                      </dd>
                    </dl>
                    <ul className="ads-notes">
                      {selected.limitations.map((l) => <li key={l}>{l}</li>)}
                    </ul>
                  </div>
                </div>
              )}

              <div className="ads-matrix">
                <div className="ads-matrix-head">
                  <h3><Icon name="table" /><span>Detailed Performance Matrix View</span></h3>
                  <button type="button" className="ads-export" onClick={() => downloadCsv(matrix)} disabled={!matrix.length}>
                    <Icon name="csv" />
                    <span>Export Matrix CSV</span>
                  </button>
                </div>
                <div className="ads-table-wrap">
                  <div className="ads-table-scroll">
                    <table className="ads-table">
                      <thead>
                        <tr>
                          <th>Geo Area</th>
                          <th>Age Bucket</th>
                          <th>Gender</th>
                          <th>RFM Segment</th>
                          <th>Top Creative (Image ID)</th>
                          <th style={{ textAlign: "right" }}>Opportunity Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {matrix.length === 0 && (
                          <tr><td colSpan={6}>No creative has enough eligible evidence (k={run.privacy_k}) to rank.</td></tr>
                        )}
                        {matrix.map((m, i) => (
                          <tr key={`${m.area}-${m.age}-${m.gender}-${i}`} className={m.score < 50 ? "dim" : undefined}>
                            <td className="area">{m.area}</td>
                            <td>{m.age}</td>
                            <td>{m.gender}</td>
                            <td className={`ads-seg-${m.rfm.split(" ")[0]}`}>{m.rfm}</td>
                            <td className="creative" title={m.imageId}>{m.creative.toUpperCase()}</td>
                            <td className="num">{m.score.toFixed(1)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                <p className="ads-foot">{run.disclaimer}</p>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
