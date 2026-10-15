"use client";

import { useEffect, useRef, useState } from "react";

type Props = {
  features: {
    type: string;
    properties: {
      pin_code?: string;
      area?: string;
      area_name?: string;
      metric_value?: number | null;
      suppressed?: boolean;
    };
    geometry: {
      type: string;
      coordinates: any;
    };
  }[];
  disclaimer?: string;
};

const TARGET_PINS = new Set(["560102", "560103", "560068", "560035", "560087", "560037"]);

function colorFor(value: number | null | undefined, max: number) {
  if (value == null || max <= 0) return "#0f6b4c";
  const t = Math.max(0, Math.min(1, value / max));
  const start = [220, 232, 225];
  const end = [15, 107, 76];
  const rgb = start.map((c, i) => Math.round(c + (end[i] - c) * t));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

export function PinChoropleth({ features, disclaimer }: Props) {
  const mapElement = useRef<HTMLDivElement | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);

  useEffect(() => {
    let map: import("leaflet").Map | undefined;
    let cancelled = false;
    let resizeObserver: ResizeObserver | undefined;

    async function renderMap() {
      try {
        const L = await import("leaflet");
        if (cancelled || !mapElement.current) return;

        const selected = features.filter((f) =>
          TARGET_PINS.has(String(f.properties.pin_code || ""))
        );
        if (!selected.length) {
          setMapError("No polygons found for the six configured Bengaluru PIN codes.");
          return;
        }

        // Prefer detailed PIN geometry from DataMeet's Bengaluru boundary dataset.
        // Keep the bundled demo geometry as a fallback if the remote source is unavailable.
        let mapFeatures = selected;
        try {
          const response = await fetch(
            "https://raw.githubusercontent.com/datameet/PincodeBoundary/master/Bangalore/boundary.geojson"
          );
          if (response.ok) {
            const boundaryData = await response.json();
            const boundaries = new Map<string, any>();
            for (const boundary of boundaryData.features || []) {
              const pin = String(boundary.properties?.pin_code || "");
              if (TARGET_PINS.has(pin)) boundaries.set(pin, boundary);
            }
            mapFeatures = selected.map((f) => {
              const boundary = boundaries.get(String(f.properties.pin_code || ""));
              return boundary
                ? {
                    ...f,
                    properties: {
                      ...f.properties,
                      area: boundary.properties?.area_name || f.properties.area,
                    },
                    geometry: boundary.geometry,
                  }
                : f;
            });
          }
        } catch {
          // The interactive basemap still works with bundled approximate demo geometry.
        }

        const values = mapFeatures
          .map((f) => f.properties.metric_value)
          .filter((v): v is number => typeof v === "number");
        const max = values.length ? Math.max(...values) : 0;

        map = L.map(mapElement.current, {
          scrollWheelZoom: true,
          zoomControl: true,
        });

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(map);

        const collection = {
          type: "FeatureCollection" as const,
          features: mapFeatures.map((f) => ({
            type: "Feature" as const,
            properties: f.properties,
            geometry: f.geometry as any,
          })),
        };

        const layer = L.geoJSON(collection as any, {
          style: (feature) => {
            const props = feature?.properties || {};
            const suppressed = Boolean(props.suppressed);
            return {
              color: "#16352a",
              weight: 2,
              opacity: 0.95,
              fillColor: suppressed ? "#eceff1" : colorFor(props.metric_value, max),
              fillOpacity: suppressed ? 0.68 : 0.62,
            };
          },
          onEachFeature: (feature, layer) => {
            const props = feature.properties || {};
            const pin = String(props.pin_code || "");
            const area = props.area || props.area_name || "PIN area";
            const title = `<strong>${String(area)}</strong><br/>PIN: ${pin}`;
            const detail = props.suppressed
              ? "<br/><span>Customer count suppressed for privacy</span>"
              : `<br/>Active customers: ${props.metric_value ?? "No data"}`;
            layer.bindPopup(title + detail);
            layer.bindTooltip(`${pin} · ${String(area)}`);
          },
        }).addTo(map);

        if (layer.getBounds().isValid()) {
          map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: 13 });
        } else {
          map.setView([12.93, 77.68], 11);
        }

        // Ensure Leaflet recalculates the viewport when the panel finishes laying out.
        resizeObserver = new ResizeObserver(() => map?.invalidateSize());
        resizeObserver.observe(mapElement.current);
        window.setTimeout(() => map?.invalidateSize(), 100);
      } catch {
        if (!cancelled) setMapError("The map could not be loaded. Check your network connection and Leaflet installation.");
      }
    }

    renderMap();
    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      map?.remove();
    };
  }, [features]);

  return (
    <div>
      {mapError && <div className="alert">{mapError}</div>}
      <div
        ref={mapElement}
        style={{
          width: "100%",
          height: 520,
          borderRadius: 12,
          overflow: "hidden",
          border: "1px solid var(--line, #d8dfda)",
          background: "#eef2ef",
        }}
        role="application"
        aria-label="Interactive Bengaluru PIN-code map"
      />
      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "center", marginTop: "0.75rem", fontSize: "0.85rem" }}>
        <span><span style={{ display: "inline-block", width: 12, height: 12, background: "#0f6b4c", marginRight: 6, border: "1px solid #16352a" }} />Customer activity</span>
        <span><span style={{ display: "inline-block", width: 12, height: 12, background: "#eceff1", marginRight: 6, border: "1px solid #16352a" }} />Suppressed for privacy</span>
      </div>
      {disclaimer && (
        <p className="sub" style={{ fontSize: "0.8rem", marginTop: "0.5rem" }}>
          {disclaimer}
        </p>
      )}
    </div>
  );
}
