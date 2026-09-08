"use client";

import { FEATURE_LABELS, fmt } from "@/lib/risk";
import type { RiskView } from "@/lib/types";

interface Props {
  risk: RiskView | null;
}

/** "Why this alert" — SHAP contributions (log-odds) per feature. */
export default function ShapPanel({ risk }: Props) {
  if (!risk) {
    return (
      <Panel title="Why this alert">
        <p className="text-sm text-slate-400">No risk score available for this region.</p>
      </Panel>
    );
  }
  const rows = Object.entries(risk.shap)
    .filter(([f]) => !f.startsWith("doy_"))
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const season = Object.entries(risk.shap)
    .filter(([f]) => f.startsWith("doy_"))
    .reduce((s, [, v]) => s + v, 0);
  const max = Math.max(...rows.map(([, v]) => Math.abs(v)), Math.abs(season), 0.01);

  return (
    <Panel title="Why this alert" subtitle="SHAP contribution to log-odds · red pushes risk up, blue pulls it down">
      <ul className="space-y-1.5">
        {rows.map(([f, v]) => (
          <Row key={f} label={FEATURE_LABELS[f] ?? f} value={v} max={max} feature={risk.features[f]} />
        ))}
        <Row label="Season (day of year)" value={season} max={max} />
      </ul>
      <p className="mt-3 text-xs text-slate-400">
        Base value {fmt.num(risk.baseValue, 2)} · sum of bars + base = model log-odds → {fmt.pct(risk.score)}
      </p>
    </Panel>
  );
}

function Row({ label, value, max, feature }: { label: string; label2?: string; value: number; max: number; feature?: number }) {
  const pct = Math.min(100, (Math.abs(value) / max) * 100);
  const up = value > 0;
  return (
    <li className="text-xs">
      <div className="flex justify-between text-slate-300">
        <span>
          {label}
          {feature != null && <span className="text-slate-500"> = {fmt.num(feature, 1)}</span>}
        </span>
        <span className={up ? "text-red-300" : "text-sky-300"}>
          {up ? "+" : ""}
          {value.toFixed(2)}
        </span>
      </div>
      <div className="mt-0.5 h-2 w-full rounded bg-slate-700/60 relative overflow-hidden">
        <div
          className={`absolute top-0 h-full ${up ? "bg-red-500" : "bg-sky-500"}`}
          style={{ width: `${pct / 2}%`, left: up ? "50%" : `${50 - pct / 2}%` }}
        />
        <div className="absolute left-1/2 top-0 h-full w-px bg-slate-400/60" />
      </div>
    </li>
  );
}

export function Panel({ title, subtitle, children, right }: { title: string; subtitle?: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-700/70 bg-slate-800/60 p-4 shadow">
      <header className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
          {subtitle && <p className="text-[11px] text-slate-400">{subtitle}</p>}
        </div>
        {right}
      </header>
      {children}
    </section>
  );
}
