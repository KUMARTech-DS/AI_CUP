
"use client";

import { useEffect, useMemo } from "react";
import {
  MapContainer,
  TileLayer,
  GeoJSON,
  useMap,
} from "react-leaflet";
import type {
  Feature as GeoJSONFeature,
  FeatureCollection,
  Geometry,
} from "geojson";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

type Feature = {
  type: string;
  properties: {
    pin_code?: string;
    area?: string;
    metric_value?: number | null;
    suppressed?: boolean;
  };
  geometry: {
    type: string;
    coordinates: any;
  };
};

type Props = {
  features: Feature[];
  disclaimer?: string;
};

function colorFor(
  value: number | null | undefined,
  max: number
) {
  if (value == null || max <= 0) {
    return "#cbd5e1";
  }

  const t = Math.min(1, value / max);
  const start = [220, 252, 231];
  const end = [21, 128, 61];

  return `rgb(${start
    .map((v, i) =>
      Math.round(v + (end[i] - v) * t)
    )
    .join(",")})`;
}

function FitBounds({
  data,
}: {
  data: FeatureCollection;
}) {
  const map = useMap();

  useEffect(() => {
    const layer = L.geoJSON(data as any);
    const bounds = layer.getBounds();

    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.12), {
        maxZoom: 13,
      });
    }
  }, [data, map]);

  return null;
}

export function PinChoropleth({
  features,
  disclaimer,
}: Props) {
  const values = features
    .map((f) => f.properties.metric_value)
    .filter(
      (v): v is number => typeof v === "number"
    );

  const max = values.length
    ? Math.max(...values)
    : 0;

  const data = useMemo<FeatureCollection>(
    () => ({
      type: "FeatureCollection",
      features: features.map(
        (f) =>
          ({
            type: "Feature",
            properties: f.properties,
            geometry: f.geometry as Geometry,
          }) as GeoJSONFeature
      ),
    }),
    [features]
  );

  if (!features.length) {
    return (
      <div className="alert">
        No PIN polygons available.
      </div>
    );
  }

  const style = (feature?: GeoJSONFeature) => {
    const p = (feature?.properties || {}) as
      Feature["properties"];

    const suppressed = Boolean(p.suppressed);

    return {
      color: suppressed ? "#94a3b8" : "#166534",
      weight: 2,
      opacity: 0.95,
      fillColor: suppressed
        ? "#e2e8f0"
        : colorFor(p.metric_value, max),
      fillOpacity: suppressed ? 0.55 : 0.72,
    };
  };

  const onEachFeature = (
    feature: GeoJSONFeature,
    layer: L.Layer
  ) => {
    const p = (feature.properties || {}) as
      Feature["properties"];

    const title =
      p.area || p.pin_code || "PIN area";

    const detail = p.suppressed
      ? "Customer count suppressed for privacy"
      : `Active customers: ${p.metric_value ?? "—"}`;

    layer.bindTooltip(
      `${title}<br/>${detail}`
    );

    layer.on({
      mouseover: (e: L.LeafletMouseEvent) => {
        (e.target as L.Path).setStyle({
          weight: 3,
          color: "#0f172a",
          fillOpacity: 0.9,
        });
      },
      mouseout: (e: L.LeafletMouseEvent) => {
        (e.target as L.Path).setStyle(
          style(feature)
        );
      },
    });
  };

  return (
    <section className="geo-map-shell">
      <div className="geo-map-toolbar">
        <div>
          <span className="geo-live-dot" />
          Bengaluru · PIN-code coverage
        </div>

        <span className="geo-map-hint">
          Scroll to zoom · Select a boundary for details
        </span>
      </div>

      <MapContainer
        center={[12.9716, 77.5946]}
        zoom={11}
        scrollWheelZoom
        className="geo-leaflet-map"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        <GeoJSON
          key={`${features.length}-${max}`}
          data={data as any}
          style={style as any}
          onEachFeature={onEachFeature as any}
        />

        <FitBounds data={data} />
      </MapContainer>

      <div className="geo-map-footer">
        <div className="geo-legend">
          <span
            className="geo-legend-swatch"
            style={{ background: "#15803d" }}
          />
          Higher activity
        </div>

        <div className="geo-legend">
          <span
            className="geo-legend-swatch"
            style={{
              background: "#dcfce7",
              border: "1px solid #86efac",
            }}
          />
          Lower activity
        </div>

        <div className="geo-legend">
          <span
            className="geo-legend-swatch"
            style={{
              background: "#e2e8f0",
              border: "1px solid #94a3b8",
            }}
          />
          Privacy suppressed
        </div>
      </div>

      {disclaimer && (
        <p className="geo-map-disclaimer">
          {disclaimer}
        </p>
      )}
    </section>
  );
}
