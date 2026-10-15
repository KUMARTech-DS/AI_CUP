"use client";

import "leaflet/dist/leaflet.css";
import "./command-center.css";

import { useEffect, useMemo, useRef, useState } from "react";
import type * as Leaflet from "leaflet";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiGet } from "@/lib/api";
import { garmentColorHex, titleCase } from "@/lib/garmentColors";
import { GarmentCanvas } from "./GarmentCanvas";

type TopItem = { value: string; count: number; share: number | null };

type GarmentProfile = {
  garments: number | null;
  customers: number | null;
  suppressed: boolean;
  size_curve?: { size: string; count: number; share: number }[];
  materials?: TopItem[];
  colors?: TopItem[];
  styles?: TopItem[];
  commodity_types?: TopItem[];
  patterns?: TopItem[];
  brand_mix?: { brand: string; garments: number; share: number; is_own: boolean }[];
  competitor_share?: number | null;
};

type Hub = {
  pin_code: string;
  area: string;
  lat: number | null;
  lon: number | null;
  polygon: [number, number][] | null;
  location_precision: string | null;
  suppressed: boolean;
  active_customers?: number;
  orders_per_month?: number;
  revenue_per_month?: number;
  avg_order_value?: number | null;
  repeat_rate?: number;
  served_local_pct?: number | null;
  stores_in_pin?: number;
  nearest_store?: { store_name: string; area: string; pin_code: string; km: number } | null;
  catchment?: { radius_km: number; customers: number | null; suppressed: boolean }[] | null;
  cancel_rate?: number;
  complaint_rate?: number;
  complaints?: number;
  orders_total?: number;
  commodities?: TopItem[];
  garments?: GarmentProfile;
  rank?: number;
  grade?: string;
};

type Store = {
  store_id: string;
  store_name: string;
  area: string;
  pin_code: string;
  status: string | null;
  premium: boolean;
  lat: number;
  lon: number;
  orders_per_day: number;
  location_precision: string;
};

type CommandCenterData = {
  kpis: {
    hubs: number;
    orders_per_month: number;
    revenue_per_month: number;
    competitor_share: number | null;
    served_local_pct: number | null;
  };
  hubs: Hub[];
  stores: Store[];
  heat: { lat: number; lon: number; size_deg: number; customers: number }[];
  garments_overall: GarmentProfile;
  window: { from: string | null; to: string | null; months: number };
  privacy_k: number;
  metric_version: string;
  notes: Record<string, string>;
};

type TabId = "site" | "stock" | "logistics" | "returns" | "brands";
type LayerId = "heat" | "catchment" | "competitors" | "stores";

const TABS: { id: TabId; label: string }[] = [
  { id: "site", label: "1. Site & Demand" },
  { id: "stock", label: "2. Sizing & Stock" },
  { id: "logistics", label: "3. Logistics" },
  { id: "returns", label: "4. Cancellations & Complaints" },
  { id: "brands", label: "5. Competitor Brands" },
];

const RING_COLORS = ["#00f3ff", "#8a2be2", "#ff007f"];
const SIZE_COLORS = ["#00f3ff", "#8a2be2", "#00ff88", "#ff007f", "#eab308", "#38bdf8", "#f97316"];
const GRADE_BADGE: Record<string, string> = { "A+": "cc-badge-emerald", A: "cc-badge-cyan", B: "cc-badge-violet", C: "cc-badge-muted" };

function fmtInt(n: number | null | undefined): string {
  return n == null ? "—" : Math.round(n).toLocaleString("en-IN");
}

function fmtInr(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(1)} L`;
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

function pct(n: number | null | undefined, digits = 1): string {
  return n == null ? "—" : `${(n * 100).toFixed(digits)}%`;
}

function haversineKm(a: [number, number], b: [number, number]): number {
  const r = (d: number) => (d * Math.PI) / 180;
  const dLat = r(b[0] - a[0]);
  const dLon = r(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(r(a[0])) * Math.cos(r(b[0])) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(h));
}

function liveProfile(p: GarmentProfile | undefined | null): GarmentProfile | null {
  return p && !p.suppressed && (p.garments || 0) > 0 ? p : null;
}

function csvCell(v: string | number): string {
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function CommandCenter() {
  const [data, setData] = useState<CommandCenterData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedPin, setSelectedPin] = useState<string>("all");
  const [tab, setTab] = useState<TabId>("site");
  const [layers, setLayers] = useState<Record<LayerId, boolean>>({
    heat: true,
    catchment: true,
    competitors: true,
    stores: true,
  });
  const [colorOverride, setColorOverride] = useState<string | null>(null);
  const [showManifest, setShowManifest] = useState(false);
  const [showFinder, setShowFinder] = useState(false);
  const [brandQuery, setBrandQuery] = useState("");
  const [manifestUnits, setManifestUnits] = useState(1000);

  const mapEl = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Leaflet.Map | null>(null);
  const LRef = useRef<typeof Leaflet | null>(null);
  const groups = useRef<Partial<Record<"hubs" | LayerId, Leaflet.LayerGroup>>>({});
  const [mapReady, setMapReady] = useState(false);

  useEffect(() => {
    apiGet<CommandCenterData>("/v1/geo/command-center")
      .then((res) => {
        if (res.errors?.length) setError(res.errors[0].message);
        else {
          setData(res.data);
          const top = res.data.hubs.find((h) => !h.suppressed && h.lat != null);
          if (top) setSelectedPin(top.pin_code);
        }
      })
      .catch(() => setError("Failed to load the spatial command center from the API"));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mod = await import("leaflet");
      const L = (mod as unknown as { default?: typeof Leaflet }).default ?? mod;
      if (cancelled || !mapEl.current || mapRef.current) return;
      const map = L.map(mapEl.current, { zoomControl: true }).setView([12.97, 77.59], 11);
      const esri = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";
      L.tileLayer(`${esri}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, {
        attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
        maxZoom: 16,
      }).addTo(map);
      map.createPane("cc-labels").style.zIndex = "450";
      map.getPane("cc-labels")!.style.pointerEvents = "none";
      L.tileLayer(`${esri}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 16, pane: "cc-labels" }).addTo(map);
      map.createPane("cc-heat").style.zIndex = "350";
      LRef.current = L;
      mapRef.current = map;
      groups.current = {
        heat: L.layerGroup([], { pane: "cc-heat" }),
        hubs: L.layerGroup().addTo(map),
        catchment: L.layerGroup(),
        competitors: L.layerGroup(),
        stores: L.layerGroup(),
      };
      setMapReady(true);
    })();
    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  const rankedHubs = useMemo(() => (data?.hubs || []).filter((h) => !h.suppressed), [data]);
  const mappedHubs = useMemo(() => rankedHubs.filter((h) => h.lat != null && h.lon != null), [rankedHubs]);
  const hub = useMemo(
    () => (selectedPin === "all" ? null : rankedHubs.find((h) => h.pin_code === selectedPin) || null),
    [rankedHubs, selectedPin],
  );
  const k = data?.privacy_k ?? 30;
  const profile = liveProfile(hub ? hub.garments : data?.garments_overall);
  const areaLabel = hub ? hub.area : "All hubs";

  useEffect(() => {
    setColorOverride(null);
  }, [selectedPin]);

  // Hub polygons / centroid markers
  useEffect(() => {
    const L = LRef.current;
    const g = groups.current.hubs;
    if (!mapReady || !L || !g || !data) return;
    g.clearLayers();
    const maxOrders = Math.max(1, ...mappedHubs.map((h) => h.orders_per_month || 0));
    for (const h of data.hubs) {
      if (h.lat == null || h.lon == null) continue;
      const t = h.suppressed ? 0 : (h.orders_per_month || 0) / maxOrders;
      const selected = h.pin_code === selectedPin;
      const style: Leaflet.PathOptions = {
        color: selected ? "#00ff88" : h.suppressed ? "#4b5563" : "#00f3ff",
        weight: selected ? 3 : 1.5,
        dashArray: h.suppressed ? "4 4" : undefined,
        fillColor: h.suppressed ? "#374151" : "#8a2be2",
        fillOpacity: h.suppressed ? 0.06 : 0.12 + 0.4 * t,
      };
      const layer: Leaflet.Path =
        h.polygon && h.polygon.length > 2
          ? L.polygon(h.polygon, style)
          : L.circleMarker([h.lat, h.lon], { ...style, radius: h.suppressed ? 5 : 8 + 14 * t });
      const label = h.suppressed
        ? `${h.area} · ${h.pin_code} (below k=${k})`
        : `${h.area} · ${h.pin_code} · ${fmtInt(h.orders_per_month)} orders/mo`;
      layer.bindTooltip(label, { className: "cc-tooltip", direction: "center" });
      if (!h.suppressed) layer.on("click", () => setSelectedPin(h.pin_code));
      g.addLayer(layer);
    }
  }, [mapReady, data, mappedHubs, selectedPin, k]);

  // Customer density heat cells
  useEffect(() => {
    const L = LRef.current;
    const g = groups.current.heat;
    if (!mapReady || !L || !g || !data) return;
    g.clearLayers();
    const max = Math.max(1, ...data.heat.map((c) => c.customers));
    for (const c of data.heat) {
      const t = c.customers / max;
      const rect = L.rectangle(
        [
          [c.lat, c.lon],
          [c.lat + c.size_deg, c.lon + c.size_deg],
        ],
        {
          pane: "cc-heat",
          stroke: false,
          fillColor: t > 0.66 ? "#00f3ff" : t > 0.33 ? "#22d3ee" : "#8a2be2",
          fillOpacity: 0.06 + 0.22 * t,
        },
      );
      rect.bindTooltip(`${fmtInt(c.customers)} customers in this ~2 km cell`, { className: "cc-tooltip" });
      g.addLayer(rect);
    }
  }, [mapReady, data]);

  // Catchment rings for selected hub
  useEffect(() => {
    const L = LRef.current;
    const g = groups.current.catchment;
    if (!mapReady || !L || !g) return;
    g.clearLayers();
    if (!hub || hub.lat == null || hub.lon == null) return;
    (hub.catchment || []).forEach((ring, i) => {
      g.addLayer(
        L.circle([hub.lat as number, hub.lon as number], {
          radius: ring.radius_km * 1000,
          color: RING_COLORS[i % RING_COLORS.length],
          weight: 1,
          fillColor: RING_COLORS[i % RING_COLORS.length],
          fillOpacity: 0.15 - i * 0.05,
          interactive: false,
        }),
      );
    });
  }, [mapReady, hub]);

  // Competitor brand presence (from garment extractions)
  useEffect(() => {
    const L = LRef.current;
    const g = groups.current.competitors;
    if (!mapReady || !L || !g || !data) return;
    g.clearLayers();
    for (const h of mappedHubs) {
      const p = liveProfile(h.garments);
      if (!p || p.competitor_share == null || !(p.brand_mix || []).length) continue;
      const rivals = (p.brand_mix || []).filter((b) => !b.is_own).slice(0, 4);
      const icon = L.divIcon({
        className: "",
        html: `<div style="width:${14 + 14 * p.competitor_share}px;height:${14 + 14 * p.competitor_share}px;border-radius:999px;background:#db2777;border:2px solid #fff;box-shadow:0 0 15px rgba(255,0,127,.5)"></div>`,
        iconSize: [14 + 14 * p.competitor_share, 14 + 14 * p.competitor_share],
      });
      const m = L.marker([(h.lat as number) + 0.004, (h.lon as number) + 0.004], { icon });
      const list = rivals.map((b) => `${b.brand} — ${pct(b.share, 0)}`).join("<br/>") || "No competitor brands";
      m.bindPopup(`<strong>${h.area}</strong><br/>Competitor share ${pct(p.competitor_share, 0)}<br/>${list}`);
      g.addLayer(m);
    }
  }, [mapReady, data, mappedHubs]);

  // Store network
  useEffect(() => {
    const L = LRef.current;
    const g = groups.current.stores;
    if (!mapReady || !L || !g || !data) return;
    g.clearLayers();
    for (const s of data.stores) {
      const m = L.circleMarker([s.lat, s.lon], {
        radius: 3 + Math.min(6, s.orders_per_day),
        color: "#00ff88",
        weight: 1,
        fillColor: "#00ff88",
        fillOpacity: s.status && s.status.toLowerCase() !== "active" ? 0.25 : 0.7,
      });
      m.bindPopup(
        `<strong>${s.store_name}</strong><br/>${s.area} · ${s.pin_code}<br/>${s.orders_per_day.toFixed(1)} orders/day` +
          `${s.premium ? "<br/>Premium laundry" : ""}<br/><em>Location: PIN centroid</em>`,
      );
      g.addLayer(m);
    }
  }, [mapReady, data]);

  // Layer visibility
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    (Object.keys(layers) as LayerId[]).forEach((id) => {
      const g = groups.current[id];
      if (!g) return;
      if (layers[id] && !map.hasLayer(g)) g.addTo(map);
      if (!layers[id] && map.hasLayer(g)) map.removeLayer(g);
    });
  }, [mapReady, layers]);

  // Camera
  useEffect(() => {
    const L = LRef.current;
    const map = mapRef.current;
    if (!mapReady || !L || !map) return;
    if (hub && hub.lat != null && hub.lon != null) {
      map.flyTo([hub.lat, hub.lon], 14, { duration: 1.2 });
    } else if (!hub && mappedHubs.length) {
      map.flyToBounds(L.latLngBounds(mappedHubs.map((h) => [h.lat as number, h.lon as number])), {
        padding: [40, 40],
        duration: 1.2,
      });
    }
  }, [mapReady, hub, mappedHubs]);

  // Inspector
  const palette = (profile?.colors || []).map((c) => ({ name: c.value, hex: garmentColorHex(c.value), share: c.share }));
  const garmentColor = colorOverride || palette[0]?.hex || "#1e1b2e";
  const topSizes = [...(profile?.size_curve || [])].sort((a, b) => b.share - a.share).slice(0, 2);
  const topStyle = profile?.styles?.[0];
  const topMaterial = profile?.materials?.[0];
  const topType = profile?.commodity_types?.[0];
  const serviceMix = (hub?.commodities || []).slice(0, 3);

  // Logistics
  const nearbyStores = useMemo(() => {
    if (!data) return [];
    if (hub && hub.lat != null && hub.lon != null) {
      const c: [number, number] = [hub.lat, hub.lon];
      return data.stores
        .map((s) => ({ ...s, km: haversineKm(c, [s.lat, s.lon]) }))
        .sort((a, b) => a.km - b.km)
        .slice(0, 6);
    }
    return [...data.stores]
      .sort((a, b) => b.orders_per_day - a.orders_per_day)
      .slice(0, 6)
      .map((s) => ({ ...s, km: null as number | null }));
  }, [data, hub]);
  const maxLoad = Math.max(0.01, ...nearbyStores.map((s) => s.orders_per_day));
  const rebalance = useMemo(() => {
    const active = nearbyStores.filter((s) => !s.status || s.status.toLowerCase() === "active");
    if (active.length < 2) return null;
    const busy = active.reduce((a, b) => (b.orders_per_day > a.orders_per_day ? b : a));
    const idle = active.reduce((a, b) => (b.orders_per_day < a.orders_per_day ? b : a));
    if (busy.store_id === idle.store_id || busy.orders_per_day < 1.5 * Math.max(idle.orders_per_day, 0.1)) return null;
    return { busy, idle, shift: (busy.orders_per_day - idle.orders_per_day) / 2 };
  }, [nearbyStores]);
  const storesInPinLoad = hub && data ? data.stores.filter((s) => s.pin_code === hub.pin_code).reduce((a, s) => a + s.orders_per_day, 0) : null;

  // Cancellations & complaints network averages (order-weighted)
  const network = useMemo(() => {
    let orders = 0;
    let cancels = 0;
    let complaints = 0;
    for (const h of rankedHubs) {
      orders += h.orders_total || 0;
      cancels += (h.cancel_rate || 0) * (h.orders_total || 0);
      complaints += h.complaints || 0;
    }
    return {
      orders,
      cancel_rate: orders ? cancels / orders : null,
      complaint_rate: orders ? complaints / orders : null,
      complaints,
    };
  }, [rankedHubs]);

  // Competitor brand search across hubs
  const brandHits = useMemo(() => {
    const q = brandQuery.trim().toLowerCase();
    const rows: { brand: string; share: number; garments: number; is_own: boolean; hub: Hub }[] = [];
    for (const h of rankedHubs) {
      for (const b of liveProfile(h.garments)?.brand_mix || []) {
        if (!q || b.brand.toLowerCase().includes(q)) rows.push({ ...b, hub: h });
      }
    }
    return rows.sort((a, b) => b.share - a.share).slice(0, 30);
  }, [rankedHubs, brandQuery]);

  // Packing manifest
  const manifest = useMemo(() => {
    const cats = profile?.commodity_types?.length
      ? profile.commodity_types.map((c) => ({ name: c.value, share: c.share || 0 }))
      : (hub?.commodities || []).map((c) => ({ name: c.value, share: c.share || 0 }));
    const total = cats.reduce((a, c) => a + c.share, 0) || 1;
    const sizes = profile?.size_curve || [];
    const fabric = topMaterial ? titleCase(topMaterial.value) : "—";
    return cats.map((c) => {
      const units = Math.round((manifestUnits * c.share) / total);
      return {
        category: titleCase(c.name),
        fabric,
        sizes: sizes.length ? sizes.map((s) => `${s.size}:${Math.round(units * s.share)}`).join(" / ") : "No size data",
        units,
      };
    });
  }, [profile, hub, manifestUnits, topMaterial]);
  const manifestSource = profile?.commodity_types?.length ? "garment extractions" : "service line mix";

  function downloadManifest() {
    const header = ["hub", "pin_code", "category", "fabric", "size_breakup", "units"];
    const rows = manifest.map((r) => [areaLabel, hub?.pin_code || "all", r.category, r.fabric, r.sizes, r.units]);
    const csv = [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `packing_manifest_${hub?.pin_code || "all"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const toggle = (id: LayerId) => setLayers((s) => ({ ...s, [id]: !s[id] }));

  const brandResults = (
    <div className="cc-rows">
      {brandHits.length ? (
        brandHits.map((r) => (
          <button
            key={`${r.hub.pin_code}-${r.brand}`}
            type="button"
            className="cc-row cc-row-btn"
            onClick={() => {
              setSelectedPin(r.hub.pin_code);
              setShowFinder(false);
            }}
          >
            <span className="cc-row-main">
              <span className="cc-dot" style={{ background: r.is_own ? "#00ff88" : "#ff007f" }} />
              <span>
                <span className="cc-row-title">{r.brand}</span>
                <span className="cc-row-sub">
                  {r.hub.area} · {r.hub.pin_code} · {fmtInt(r.garments)} garments
                </span>
              </span>
            </span>
            <span className="cc-badge cc-badge-cyan">{pct(r.share, 0)}</span>
          </button>
        ))
      ) : (
        <div className="cc-empty">
          {brandQuery
            ? `No hub shows "${brandQuery}" among reviewed garments.`
            : `No hub has at least ${k} customers with reviewed garment brands yet. Brands appear here as extractions are reviewed.`}
        </div>
      )}
    </div>
  );

  return (
    <div className="cc-root">
      <header className="cc-header cc-glass">
        <div className="cc-brand">
          <div className="cc-logo">
            <div>S</div>
          </div>
          <div>
            <h2 className="cc-title">
              SUFFAI_SPATIAL <span className="cc-version">{data?.metric_version || "live"}</span>
            </h2>
            <p className="cc-subtitle">Bengaluru retail expansion &amp; demand command center</p>
          </div>
        </div>

        <div className="cc-controls">
          <select className="cc-select" value={selectedPin} onChange={(e) => setSelectedPin(e.target.value)}>
            <option value="all">All hubs</option>
            {rankedHubs.map((h) => (
              <option key={h.pin_code} value={h.pin_code}>
                {h.area} ({h.pin_code}) · {h.grade}
              </option>
            ))}
          </select>
          <button type="button" className="cc-btn cc-btn-scan" onClick={() => setShowFinder(true)}>
            Competitor brand finder
          </button>
        </div>

        <div className="cc-kpis">
          <div>
            <span className="cc-kpi-label">Ranked hubs</span>
            <span className="cc-kpi-value cc-c-cyan">{data ? `${rankedHubs.length} PINs` : "—"}</span>
          </div>
          <div>
            <span className="cc-kpi-label">Revenue / mo</span>
            <span className="cc-kpi-value cc-c-emerald">{fmtInr(data?.kpis.revenue_per_month)}</span>
          </div>
          <div>
            <span className="cc-kpi-label">Orders / mo</span>
            <span className="cc-kpi-value cc-c-violet">{fmtInt(data?.kpis.orders_per_month)}</span>
          </div>
          <div>
            <span className="cc-kpi-label">Competitor share</span>
            <span className="cc-kpi-value cc-c-magenta">{pct(data?.kpis.competitor_share, 0)}</span>
          </div>
        </div>
      </header>

      <main className="cc-main">
        <section className="cc-left">
          <div className="cc-map-wrap">
            <div ref={mapEl} className="cc-map" />

            <div className="cc-layers">
              <button type="button" className={`cc-layer cc-glass cc-layer-cyan ${layers.heat ? "" : "off"}`} onClick={() => toggle("heat")}>
                <span className="cc-dot" style={{ background: "#22d3ee", boxShadow: "0 0 10px #00f3ff" }} />
                Customer demand heatmap
              </button>
              <button
                type="button"
                className={`cc-layer cc-glass cc-layer-violet ${layers.catchment ? "" : "off"}`}
                onClick={() => toggle("catchment")}
              >
                <span className="cc-dot" style={{ background: "#a78bfa", boxShadow: "0 0 10px #8a2be2" }} />
                Catchment radii
              </button>
              <button
                type="button"
                className={`cc-layer cc-glass cc-layer-magenta ${layers.competitors ? "" : "off"}`}
                onClick={() => toggle("competitors")}
              >
                <span className="cc-dot" style={{ background: "#ec4899", boxShadow: "0 0 10px #ff007f" }} />
                Competitor brand presence
              </button>
              <button type="button" className={`cc-layer cc-glass cc-layer-emerald ${layers.stores ? "" : "off"}`} onClick={() => toggle("stores")}>
                <span className="cc-dot" style={{ background: "#34d399", boxShadow: "0 0 10px #00ff88" }} />
                Store network
              </button>
            </div>

            {data && (
              <div className="cc-map-note cc-glass">
                {data.notes.stores} {hub && hub.lat == null ? "This hub has no polygon or enough geo-tagged customers to place on the map." : ""}
              </div>
            )}
          </div>

          <div className="cc-inspector cc-glass">
            <div className="cc-insp-left">
              <div className="cc-canvas-box">
                <GarmentCanvas color={garmentColor} />
                <span className="cc-canvas-tag">Interactive</span>
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                  <span className="cc-eyebrow cc-c-violet">Regional color &amp; fit match</span>
                  <span className={`cc-badge ${topStyle ? "cc-badge-cyan" : "cc-badge-muted"}`}>
                    {topStyle ? `${titleCase(topStyle.value)} · ${pct(topStyle.share, 0)}` : "Fit n/a"}
                  </span>
                </div>
                {profile ? (
                  <>
                    <h3 className="cc-insp-title">
                      {areaLabel} regional spec: {titleCase(palette[0]?.name || "")} {titleCase(topType?.value || "garment")}
                    </h3>
                    <p className="cc-insp-detail">
                      {topMaterial ? `${titleCase(topMaterial.value)} (${pct(topMaterial.share, 0)})` : "Material n/a"} • Dominant hue:{" "}
                      {titleCase(palette[0]?.name || "n/a")} ({garmentColor}) • Highest demand:{" "}
                      {topSizes.length ? topSizes.map((s) => `Size ${s.size}`).join(" & ") : "no size data"} •{" "}
                      {fmtInt(profile.garments)} garments
                    </p>
                  </>
                ) : (
                  <>
                    <h3 className="cc-insp-title">
                      {areaLabel}: garment extractions below k={k}
                    </h3>
                    <p className="cc-insp-detail">
                      {serviceMix.length
                        ? `Top service lines: ${serviceMix.map((c) => `${titleCase(c.value)} ${pct(c.share, 0)}`).join(" · ")}`
                        : "Colour, fit and size appear once enough garment images are extracted for this area."}
                    </p>
                  </>
                )}
                <div className="cc-swatches">
                  <span>Regional swatches:</span>
                  {palette.length ? (
                    palette.map((c) => (
                      <button
                        key={c.name}
                        type="button"
                        title={`${titleCase(c.name)} · ${pct(c.share, 0)}`}
                        className={`cc-swatch ${garmentColor === c.hex ? "active" : ""}`}
                        style={{ background: c.hex }}
                        onClick={() => setColorOverride(c.hex)}
                      />
                    ))
                  ) : (
                    <span>—</span>
                  )}
                </div>
              </div>
            </div>
            <div className="cc-actions">
              <button type="button" className="cc-btn cc-btn-emerald" onClick={() => setShowManifest(true)} disabled={!data}>
                Generate SKU packing manifest
              </button>
              <button type="button" className="cc-btn cc-btn-violet" onClick={() => setTab("logistics")}>
                Rebalance store load
              </button>
            </div>
          </div>
        </section>

        <aside className="cc-right">
          <div className="cc-tabs">
            {TABS.map((t) => (
              <button key={t.id} type="button" className={`cc-tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
                {t.label}
              </button>
            ))}
          </div>

          <div className="cc-panel">
            {error && <div className="cc-empty cc-error">{error}</div>}
            {!data && !error && <div className="cc-loading">Loading spatial intelligence…</div>}

            {data && tab === "site" && (
              <>
                <div className="cc-card cc-glass cc-card-violet">
                  <div className="cc-card-h">
                    <div>
                      <span className="cc-eyebrow cc-c-cyan">{hub ? "Expansion hub analysis" : "Network overview"}</span>
                      <h3 className="cc-hub-title">{hub ? `${hub.area} · ${hub.pin_code}` : "All PIN hubs"}</h3>
                    </div>
                    {hub?.grade && <span className={`cc-badge ${GRADE_BADGE[hub.grade] || "cc-badge-muted"}`}>Grade {hub.grade}</span>}
                  </div>
                  <p className="cc-p">
                    {hub
                      ? `Rank #${hub.rank} of ${rankedHubs.length} PIN hubs by order volume. ${fmtInt(hub.active_customers)} active customers placed ${fmtInt(
                          hub.orders_per_month,
                        )} orders a month over the last ${data.window.months} months; ${pct(hub.served_local_pct, 0)} were served by a store inside this PIN.`
                      : `${rankedHubs.length} PINs clear the k=${k} privacy floor across ${data.stores.length} stores. Window: ${data.window.from || "—"} to ${
                          data.window.to || "—"
                        }.`}
                  </p>
                </div>

                <div className="cc-card cc-glass">
                  <div className="cc-card-h">
                    <h4 className="cc-card-title">Served in-PIN vs. cross-PIN demand</h4>
                  </div>
                  {(() => {
                    const local = hub ? hub.served_local_pct : data.kpis.served_local_pct;
                    if (local == null) return <div className="cc-empty">No orders with a known store in this window.</div>;
                    return (
                      <>
                        <div className="cc-split-labels">
                          <span className="cc-c-emerald">In-PIN store: {pct(local)}</span>
                          <span className="cc-c-magenta">Served from another PIN: {pct(1 - local)}</span>
                        </div>
                        <div className="cc-split">
                          <div className="cc-split-a" style={{ width: `${local * 100}%` }} />
                          <div className="cc-split-b" style={{ width: `${(1 - local) * 100}%` }} />
                        </div>
                        <p className="cc-note">
                          A high cross-PIN share means customers here travel or ship to other stores — unmet local capacity.
                        </p>
                      </>
                    );
                  })()}
                  <div className="cc-grid2 cc-top">
                    <div className="cc-tile">
                      <span className="cc-tile-label">Orders / month</span>
                      <span className="cc-tile-value cc-c-cyan">{fmtInt(hub ? hub.orders_per_month : data.kpis.orders_per_month)}</span>
                    </div>
                    <div className="cc-tile">
                      <span className="cc-tile-label">Revenue / month</span>
                      <span className="cc-tile-value cc-c-emerald">{fmtInr(hub ? hub.revenue_per_month : data.kpis.revenue_per_month)}</span>
                    </div>
                    {hub && (
                      <>
                        <div className="cc-tile">
                          <span className="cc-tile-label">Avg order value · repeat rate</span>
                          <span className="cc-tile-value cc-c-violet">
                            {fmtInr(hub.avg_order_value)} · {pct(hub.repeat_rate, 0)}
                          </span>
                        </div>
                        <div className="cc-tile">
                          <span className="cc-tile-label">Nearest existing store</span>
                          <span className="cc-tile-value">
                            {hub.nearest_store ? `${hub.nearest_store.area} (${hub.nearest_store.km} km)` : "—"}
                          </span>
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {hub ? (
                  <div className="cc-card cc-glass">
                    <h4 className="cc-card-title" style={{ marginBottom: "0.6rem" }}>
                      Catchment customers
                    </h4>
                    {hub.catchment?.length ? (
                      <div className="cc-rows">
                        {hub.catchment.map((ring, i) => (
                          <div key={ring.radius_km} className="cc-row">
                            <span className="cc-row-main">
                              <span className="cc-dot" style={{ background: RING_COLORS[i], boxShadow: `0 0 10px ${RING_COLORS[i]}` }} />
                              {ring.radius_km} km radius
                            </span>
                            <span className="cc-val">{ring.suppressed ? `< ${k} (hidden)` : `${fmtInt(ring.customers)} customers`}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="cc-empty">This hub has no map location, so catchment rings can’t be computed.</div>
                    )}
                    <p className="cc-note">{data.notes.catchment}</p>
                  </div>
                ) : (
                  <div className="cc-card cc-glass">
                    <h4 className="cc-card-title" style={{ marginBottom: "0.6rem" }}>
                      Top hubs by order volume
                    </h4>
                    <div className="cc-rows">
                      {rankedHubs.slice(0, 8).map((h) => (
                        <button key={h.pin_code} type="button" className="cc-row cc-row-btn" onClick={() => setSelectedPin(h.pin_code)}>
                          <span className="cc-row-main">
                            <span className={`cc-badge ${GRADE_BADGE[h.grade || ""] || "cc-badge-muted"}`}>{h.grade}</span>
                            <span>
                              <span className="cc-row-title">{h.area}</span>
                              <span className="cc-row-sub">
                                {h.pin_code} · {fmtInt(h.active_customers)} customers
                              </span>
                            </span>
                          </span>
                          <span className="cc-val cc-c-cyan">{fmtInt(h.orders_per_month)}/mo</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}

            {data && tab === "stock" && (
              <>
                <div className="cc-card cc-glass">
                  <div className="cc-card-h">
                    <h4 className="cc-card-title">Localized size curve</h4>
                    <span className="cc-badge cc-badge-cyan">Vertex extractions</span>
                  </div>
                  <p className="cc-p-muted">Size labels read from garment images attributed to {areaLabel}.</p>
                  {profile?.size_curve?.length ? (
                    <div className="cc-chart">
                      <ResponsiveContainer>
                        <BarChart data={profile.size_curve.map((s) => ({ size: s.size, share: Math.round(s.share * 1000) / 10 }))}>
                          <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                          <XAxis dataKey="size" tick={{ fill: "#9ca3af", fontSize: 10 }} axisLine={false} tickLine={false} />
                          <YAxis tick={{ fill: "#9ca3af", fontSize: 10 }} axisLine={false} tickLine={false} unit="%" width={36} />
                          <Tooltip
                            cursor={{ fill: "rgba(255,255,255,0.04)" }}
                            contentStyle={{ background: "#111827", border: "1px solid #1f2937", fontSize: 12 }}
                            formatter={(v) => [`${v}%`, "Share"]}
                          />
                          <Bar dataKey="share" radius={[6, 6, 0, 0]}>
                            {profile.size_curve.map((s, i) => (
                              <Cell key={s.size} fill={SIZE_COLORS[i % SIZE_COLORS.length]} fillOpacity={0.55} stroke={SIZE_COLORS[i % SIZE_COLORS.length]} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <div className="cc-empty">
                      {profile ? "No readable size labels in these garments yet." : `Fewer than ${k} customers have extracted garments here — size curve hidden.`}
                    </div>
                  )}
                </div>

                <div className="cc-card cc-glass">
                  <h4 className="cc-card-title" style={{ marginBottom: "0.6rem" }}>
                    Assortment &amp; fabric affinity
                  </h4>
                  {profile ? (
                    <div className="cc-rows">
                      <div className="cc-row">
                        <span>
                          <span className="cc-row-title">Dominant fit / style</span>
                          <span className="cc-row-sub">{topStyle ? titleCase(topStyle.value) : "No style tags"}</span>
                        </span>
                        <span className="cc-val cc-c-violet">{topStyle ? `${pct(topStyle.share, 0)} share` : "—"}</span>
                      </div>
                      <div className="cc-row">
                        <span>
                          <span className="cc-row-title">Fabric preference</span>
                          <span className="cc-row-sub">{(profile.materials || []).map((m) => titleCase(m.value)).join(", ") || "—"}</span>
                        </span>
                        <span className="cc-val cc-c-cyan">{topMaterial ? pct(topMaterial.share, 0) : "—"}</span>
                      </div>
                      <div className="cc-row">
                        <span>
                          <span className="cc-row-title">Pattern</span>
                          <span className="cc-row-sub">{(profile.patterns || []).map((m) => titleCase(m.value)).join(", ") || "—"}</span>
                        </span>
                        <span className="cc-val cc-c-emerald">{profile.patterns?.[0] ? pct(profile.patterns[0].share, 0) : "—"}</span>
                      </div>
                      <div className="cc-row">
                        <span>
                          <span className="cc-row-title">Regional palette</span>
                          <span className="cc-row-sub">{palette.map((c) => titleCase(c.name)).join(", ") || "—"}</span>
                        </span>
                        <span className="cc-palette">
                          {palette.map((c) => (
                            <span key={c.name} style={{ background: c.hex }} title={c.name} />
                          ))}
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div className="cc-empty">{data.notes.garments}</div>
                  )}
                </div>

                {hub && (
                  <div className="cc-card cc-glass">
                    <h4 className="cc-card-title" style={{ marginBottom: "0.6rem" }}>
                      Service line mix (orders)
                    </h4>
                    {hub.commodities?.length ? (
                      <div className="cc-rows">
                        {hub.commodities.map((c) => (
                          <div key={c.value}>
                            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.74rem" }}>
                              <span>{titleCase(c.value)}</span>
                              <span className="cc-val">{pct(c.share, 0)}</span>
                            </div>
                            <div className="cc-bar">
                              <div style={{ width: `${(c.share || 0) * 100}%`, background: "linear-gradient(90deg,#8a2be2,#00f3ff)" }} />
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="cc-empty">No commodity lines for this PIN.</div>
                    )}
                  </div>
                )}
              </>
            )}

            {data && tab === "logistics" && (
              <>
                <div className="cc-card cc-glass">
                  <div className="cc-card-h">
                    <h4 className="cc-card-title">Store-level fulfilment</h4>
                    <span className="cc-badge cc-badge-emerald">{hub ? `${hub.stores_in_pin ?? 0} stores in PIN` : `${data.stores.length} stores`}</span>
                  </div>
                  <div className="cc-grid2">
                    <div className="cc-tile">
                      <span className="cc-tile-label">{hub ? "In-PIN store orders / day" : "Network orders / day"}</span>
                      <span className="cc-tile-value cc-c-emerald">
                        {hub ? (storesInPinLoad ?? 0).toFixed(1) : data.stores.reduce((a, s) => a + s.orders_per_day, 0).toFixed(1)}
                      </span>
                    </div>
                    <div className="cc-tile">
                      <span className="cc-tile-label">Served by an in-PIN store</span>
                      <span className="cc-tile-value cc-c-cyan">{pct(hub ? hub.served_local_pct : data.kpis.served_local_pct, 0)}</span>
                    </div>
                  </div>
                </div>

                <div className="cc-card cc-glass">
                  <div className="cc-card-h">
                    <h4 className="cc-card-title">{hub ? "Nearest stores & load" : "Busiest stores"}</h4>
                  </div>
                  <div className="cc-rows">
                    {nearbyStores.map((s) => (
                      <div key={s.store_id} className="cc-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
                          <span>
                            <span className="cc-row-title">{s.store_name}</span>
                            <span className="cc-row-sub">
                              {s.area} · {s.pin_code}
                              {s.status ? ` · ${s.status}` : ""}
                              {s.premium ? " · premium" : ""}
                            </span>
                          </span>
                          <span style={{ textAlign: "right" }}>
                            <span className="cc-val cc-c-emerald" style={{ display: "block" }}>
                              {s.orders_per_day.toFixed(1)}/day
                            </span>
                            {s.km != null && <span className="cc-row-sub">{s.km.toFixed(1)} km</span>}
                          </span>
                        </div>
                        <div className="cc-bar">
                          <div style={{ width: `${(s.orders_per_day / maxLoad) * 100}%`, background: "linear-gradient(90deg,#10b981,#22d3ee)" }} />
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="cc-note">{data.notes.stores} Distances are approximate.</p>
                </div>

                <div className="cc-card cc-glass">
                  <h4 className="cc-card-title" style={{ marginBottom: "0.6rem" }}>
                    Load rebalancing suggestion
                  </h4>
                  {rebalance ? (
                    <div className="cc-row">
                      <span className="cc-row-main">
                        <span className="cc-badge cc-badge-violet">Shift</span>
                        <span>
                          <span className="cc-row-title">
                            {rebalance.busy.store_name} → {rebalance.idle.store_name}
                          </span>
                          <span className="cc-row-sub">
                            ~{rebalance.shift.toFixed(1)} orders/day ({rebalance.busy.orders_per_day.toFixed(1)} vs {rebalance.idle.orders_per_day.toFixed(1)} per day)
                          </span>
                        </span>
                      </span>
                    </div>
                  ) : (
                    <div className="cc-empty">Nearby active stores carry a similar load — no rebalancing needed.</div>
                  )}
                  <p className="cc-note">Suggestion compares order load among the stores listed above; it does not move inventory.</p>
                </div>
              </>
            )}

            {data && tab === "returns" && (
              <>
                <div className="cc-card cc-glass">
                  <div className="cc-card-h">
                    <h4 className="cc-card-title">Order friction</h4>
                    <span className="cc-badge cc-badge-magenta">{areaLabel}</span>
                  </div>
                  <p className="cc-p-muted">{data.notes.returns}</p>
                  <div className="cc-grid2">
                    <div className="cc-tile">
                      <span className="cc-tile-label">Cancellation rate</span>
                      <span className="cc-tile-value cc-c-magenta">{pct(hub ? hub.cancel_rate : network.cancel_rate)}</span>
                    </div>
                    <div className="cc-tile">
                      <span className="cc-tile-label">Network average</span>
                      <span className="cc-tile-value cc-c-emerald">{pct(network.cancel_rate)}</span>
                    </div>
                    <div className="cc-tile">
                      <span className="cc-tile-label">Complaints per order</span>
                      <span className="cc-tile-value cc-c-yellow">{pct(hub ? hub.complaint_rate : network.complaint_rate, 2)}</span>
                    </div>
                    <div className="cc-tile">
                      <span className="cc-tile-label">Network average</span>
                      <span className="cc-tile-value cc-c-emerald">{pct(network.complaint_rate, 2)}</span>
                    </div>
                  </div>
                </div>
                <div className="cc-card cc-glass">
                  <h4 className="cc-card-title" style={{ marginBottom: "0.6rem" }}>
                    Volume behind the rates
                  </h4>
                  <div className="cc-rows">
                    <div className="cc-row">
                      <span>
                        <span className="cc-row-title">Orders in window</span>
                        <span className="cc-row-sub">All statuses, order PIN = {hub ? hub.pin_code : "any ranked PIN"}</span>
                      </span>
                      <span className="cc-val cc-c-cyan">{fmtInt(hub ? hub.orders_total : network.orders)}</span>
                    </div>
                    <div className="cc-row">
                      <span>
                        <span className="cc-row-title">Complaints logged</span>
                        <span className="cc-row-sub">From the complaints feed, matched by order</span>
                      </span>
                      <span className="cc-val cc-c-yellow">{fmtInt(hub ? hub.complaints : network.complaints)}</span>
                    </div>
                  </div>
                  {network.complaints === 0 && (
                    <p className="cc-note">No complaints are loaded yet — re-run the BigQuery sync to populate fact_complaint.</p>
                  )}
                </div>
              </>
            )}

            {data && tab === "brands" && (
              <>
                <div className="cc-card cc-glass">
                  <div className="cc-card-h">
                    <h4 className="cc-card-title">Competitor density &amp; whitespace</h4>
                    <span className="cc-badge cc-badge-yellow">
                      Gap score: {profile?.competitor_share != null ? `${Math.round(profile.competitor_share * 100)}/100` : "n/a"}
                    </span>
                  </div>
                  <p className="cc-p-muted">
                    Share of reviewed garments in {areaLabel} from brands other than yours. High scores mean customers already buy the category —
                    from someone else.
                  </p>
                  <div className="cc-inline">
                    <input
                      className="cc-input"
                      placeholder="Search a brand across all hubs…"
                      value={brandQuery}
                      onChange={(e) => setBrandQuery(e.target.value)}
                    />
                  </div>
                </div>
                <div className="cc-card cc-glass">
                  <div className="cc-card-h">
                    <h4 className="cc-card-title">{brandQuery ? "Matching hubs" : `${areaLabel} brand mix`}</h4>
                    <span className="cc-mono cc-c-cyan" style={{ fontSize: "0.66rem" }}>
                      {brandQuery ? `${brandHits.length} hits` : `${profile?.brand_mix?.length || 0} brands`}
                    </span>
                  </div>
                  {brandQuery ? (
                    brandResults
                  ) : profile?.brand_mix?.length ? (
                    <div className="cc-rows">
                      {profile.brand_mix.map((b) => (
                        <div key={b.brand}>
                          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.74rem" }}>
                            <span>
                              {b.brand} {b.is_own && <span className="cc-badge cc-badge-emerald">you</span>}
                            </span>
                            <span className="cc-val">{pct(b.share, 0)}</span>
                          </div>
                          <div className="cc-bar">
                            <div style={{ width: `${b.share * 100}%`, background: b.is_own ? "#00ff88" : "linear-gradient(90deg,#ec4899,#9333ea)" }} />
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="cc-empty">
                      Brand mix needs at least {k} customers with reviewed garments in {areaLabel}. Review extractions on the Garments tab to unlock
                      it.
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </aside>
      </main>

      {showFinder && (
        <div className="cc-modal-back" onClick={() => setShowFinder(false)}>
          <div className="cc-modal cc-modal-cyan cc-glass" onClick={(e) => e.stopPropagation()}>
            <div className="cc-modal-h">
              <div>
                <h3 className="cc-modal-title">Competitor brand finder</h3>
                <p className="cc-subtitle">Find where a brand shows up in customers’ wardrobes, by PIN hub</p>
              </div>
              <button type="button" className="cc-close" onClick={() => setShowFinder(false)} aria-label="Close">
                ×
              </button>
            </div>
            <div className="cc-modal-b">
              <div>
                <label className="cc-field-label" htmlFor="cc-brand-q">
                  Brand name
                </label>
                <input
                  id="cc-brand-q"
                  className="cc-input cc-mono"
                  autoFocus
                  placeholder="e.g. Zara, H&M, Levi's"
                  value={brandQuery}
                  onChange={(e) => setBrandQuery(e.target.value)}
                />
              </div>
              {brandResults}
            </div>
          </div>
        </div>
      )}

      {showManifest && data && (
        <div className="cc-modal-back" onClick={() => setShowManifest(false)}>
          <div className="cc-modal cc-modal-emerald cc-glass" onClick={(e) => e.stopPropagation()}>
            <div className="cc-modal-h">
              <div>
                <h3 className="cc-modal-title">Initial SKU packing manifest</h3>
                <p className="cc-subtitle">
                  Allocation for {areaLabel} from the {manifestSource} and local size curve
                </p>
              </div>
              <button type="button" className="cc-close" onClick={() => setShowManifest(false)} aria-label="Close">
                ×
              </button>
            </div>
            <div className="cc-modal-b">
              <div className="cc-grid3">
                <div className="cc-tile">
                  <label className="cc-tile-label" htmlFor="cc-units">
                    Total units to allocate
                  </label>
                  <input
                    id="cc-units"
                    className="cc-input cc-mono"
                    type="number"
                    min={50}
                    step={50}
                    value={manifestUnits}
                    onChange={(e) => setManifestUnits(Math.max(0, Number(e.target.value) || 0))}
                  />
                </div>
                <div className="cc-tile">
                  <span className="cc-tile-label">Primary fabric</span>
                  <span className="cc-tile-value cc-c-cyan">{topMaterial ? titleCase(topMaterial.value) : "—"}</span>
                </div>
                <div className="cc-tile">
                  <span className="cc-tile-label">Primary sizes</span>
                  <span className="cc-tile-value cc-c-violet">
                    {topSizes.length ? `${topSizes.map((s) => s.size).join(" & ")} (${pct(topSizes.reduce((a, s) => a + s.share, 0), 0)})` : "—"}
                  </span>
                </div>
              </div>
              {manifest.length ? (
                <table className="cc-table">
                  <thead>
                    <tr>
                      <th>Category</th>
                      <th>Fabric</th>
                      <th>Size breakup</th>
                      <th className="num">Units</th>
                    </tr>
                  </thead>
                  <tbody>
                    {manifest.map((r) => (
                      <tr key={r.category}>
                        <td>{r.category}</td>
                        <td>{r.fabric}</td>
                        <td className="cc-mono" style={{ fontSize: "0.68rem" }}>
                          {r.sizes}
                        </td>
                        <td className="num">{fmtInt(r.units)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="cc-empty">No category mix is available for {areaLabel} above the privacy floor.</div>
              )}
            </div>
            <div className="cc-modal-f">
              <button type="button" className="cc-btn" onClick={() => setShowManifest(false)}>
                Close
              </button>
              <button type="button" className="cc-btn cc-btn-grad" onClick={downloadManifest} disabled={!manifest.length}>
                Export manifest (CSV)
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
