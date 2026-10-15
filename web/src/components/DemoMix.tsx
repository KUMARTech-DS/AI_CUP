"use client";

type MixRow = { value: string; customers?: number | null; count?: number; suppressed?: boolean };

export type Demographics = {
  cohort_customers?: number | null;
  suppressed?: boolean;
  gender_mix?: MixRow[];
  age_band_mix?: MixRow[];
  device_mix?: MixRow[];
  note?: string;
};

function MixList({ title, rows }: { title: string; rows?: MixRow[] }) {
  const list = (rows || []).filter((r) => !r.suppressed).slice(0, 6);
  if (!list.length) return null;
  return (
    <div>
      <h4 style={{ marginBottom: 4 }}>{title}</h4>
      <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
        {list.map((r) => (
          <li key={r.value}>
            {r.value}: {r.customers ?? r.count ?? "—"}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function DemoMix({ demographics, title = "Customer demographics" }: { demographics?: Demographics | null; title?: string }) {
  if (!demographics) return null;
  return (
    <div className="panel" style={{ marginTop: "1rem" }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <p className="sub">
        {demographics.note || "Joined on customer_uuid = garment user_uuid. Aggregates only (no phone/address)."}
        {demographics.cohort_customers != null ? ` · Cohort: ${demographics.cohort_customers}` : ""}
        {demographics.suppressed ? " · suppressed (below privacy k)" : ""}
      </p>
      {!demographics.suppressed && (
        <div className="split-2">
          <MixList title="Gender" rows={demographics.gender_mix} />
          <MixList title="Age band" rows={demographics.age_band_mix} />
          <MixList title="Device" rows={demographics.device_mix} />
        </div>
      )}
    </div>
  );
}
