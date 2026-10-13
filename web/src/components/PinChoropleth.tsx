"use client";

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
    coordinates: number[][][];
  };
};

type Props = {
  features: Feature[];
  disclaimer?: string;
};

function colorFor(value: number | null | undefined, max: number): string {
  if (value == null || max <= 0) return "#d9e2dc";
  const t = Math.min(1, value / max);
  const r = Math.round(15 + (12 - 15) * t);
  const g = Math.round(107 + (53 - 107) * t);
  const b = Math.round(76 + (42 - 76) * t);
  return `rgb(${r},${g},${b})`;
}

export function PinChoropleth({ features, disclaimer }: Props) {
  const values = features
    .map((f) => f.properties.metric_value)
    .filter((v): v is number => typeof v === "number");
  const max = values.length ? Math.max(...values) : 0;

  let minLon = Infinity;
  let minLat = Infinity;
  let maxLon = -Infinity;
  let maxLat = -Infinity;
  for (const f of features) {
    for (const ring of f.geometry?.coordinates || []) {
      for (const [lon, lat] of ring) {
        minLon = Math.min(minLon, lon);
        maxLon = Math.max(maxLon, lon);
        minLat = Math.min(minLat, lat);
        maxLat = Math.max(maxLat, lat);
      }
    }
  }
  if (!Number.isFinite(minLon)) {
    return <div className="alert">No PIN polygons available.</div>;
  }

  const pad = 0.01;
  minLon -= pad;
  maxLon += pad;
  minLat -= pad;
  maxLat += pad;
  const w = 640;
  const h = 420;
  const sx = (lon: number) => ((lon - minLon) / (maxLon - minLon)) * w;
  const sy = (lat: number) => h - ((lat - minLat) / (maxLat - minLat)) * h;

  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label="PIN choropleth">
        {features.map((f) => {
          const ring = f.geometry?.coordinates?.[0] || [];
          const d = ring
            .map(([lon, lat], i) => `${i === 0 ? "M" : "L"}${sx(lon)},${sy(lat)}`)
            .join(" ") + " Z";
          const fill = f.properties.suppressed
            ? "#eceff1"
            : colorFor(f.properties.metric_value, max);
          return (
            <path
              key={f.properties.pin_code || d}
              d={d}
              fill={fill}
              stroke="#16352a"
              strokeWidth={1}
            >
              <title>
                {f.properties.area || f.properties.pin_code}
                {f.properties.suppressed
                  ? " (suppressed)"
                  : `: ${f.properties.metric_value ?? "—"}`}
              </title>
            </path>
          );
        })}
      </svg>
      {disclaimer && (
        <p className="sub" style={{ fontSize: "0.8rem", marginTop: "0.5rem" }}>
          {disclaimer}
        </p>
      )}
    </div>
  );
}
