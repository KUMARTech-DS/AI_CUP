"use client";

export type DemoFilterState = { gender: string; ageBand: string; device: string };

export const EMPTY_DEMO_FILTERS: DemoFilterState = { gender: "", ageBand: "", device: "" };

const GENDERS = ["male", "female", "unknown"];
const AGE_BANDS = ["18_24", "25_34", "35_44", "45_54", "55_plus", "under_18", "unknown"];
const DEVICES = ["mobile", "web", "ios", "android", "unknown"];

/** Request-body shape shared by geo / research APIs. */
export function demoFilterBody(f: DemoFilterState) {
  return {
    ...(f.gender ? { genders: [f.gender] } : {}),
    ...(f.ageBand ? { age_bands: [f.ageBand] } : {}),
    ...(f.device ? { device_types: [f.device] } : {}),
  };
}

/** Query-string shape used by GET /v1/brands/{id}/benchmarks. */
export function demoFilterQuery(f: DemoFilterState): string {
  const p = new URLSearchParams();
  if (f.gender) p.set("genders", f.gender);
  if (f.ageBand) p.set("age_bands", f.ageBand);
  if (f.device) p.set("device_types", f.device);
  const s = p.toString();
  return s ? `?${s}` : "";
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="field" style={{ minWidth: 150 }}>
      <label>{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </div>
  );
}

export function DemoFilters({
  value,
  onChange,
  onApply,
  showDevice = true,
}: {
  value: DemoFilterState;
  onChange: (v: DemoFilterState) => void;
  onApply: (f: DemoFilterState) => void;
  showDevice?: boolean;
}) {
  return (
    <div className="panel" style={{ marginBottom: "1rem" }}>
      <div style={{ display: "flex", gap: "1rem", alignItems: "flex-end", flexWrap: "wrap" }}>
        <Select label="Customer gender" value={value.gender} options={GENDERS}
          onChange={(gender) => onChange({ ...value, gender })} />
        <Select label="Age band" value={value.ageBand} options={AGE_BANDS}
          onChange={(ageBand) => onChange({ ...value, ageBand })} />
        {showDevice && (
          <Select label="Device type" value={value.device} options={DEVICES}
            onChange={(device) => onChange({ ...value, device })} />
        )}
        <button className="btn" type="button" onClick={() => onApply(value)}>Apply filters</button>
        <button className="btn secondary" type="button"
          onClick={() => { onChange(EMPTY_DEMO_FILTERS); onApply(EMPTY_DEMO_FILTERS); }}>
          Reset
        </button>
      </div>
      <p className="sub" style={{ marginBottom: 0 }}>
        Filters use customer profiles joined on customer_uuid = garment user_uuid. Cells below privacy k are suppressed.
      </p>
    </div>
  );
}
