"use client";

import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { Panel } from "./ShapPanel";
import { DROUGHT_STYLE, fmt } from "@/lib/risk";
import type { DroughtIndex } from "@/lib/types";

interface Props {
  drought: DroughtIndex | null;
  loading?: boolean;
  error?: string | null;
}

const spiWord = (v: number | null) =>
  v == null ? "—" : v <= -2 ? "extremely dry" : v <= -1.5 ? "severely dry" : v <= -1 ? "moderately dry" : v <= -0.5 ? "mildly dry" : v < 1 ? "near normal" : "wet";

export default function DroughtCard({ drought, loading, error }: Props) {
  if (!drought) {
    return (
      <Panel title="Drought index">
        <p className="text-sm text-slate-400">{loading ? "Loading…" : error ?? "No drought snapshot."}</p>
      </Panel>
    );
  }
  const cat = drought.composite.category;
  const style = DROUGHT_STYLE[cat] ?? DROUGHT_STYLE.normal;
  const series = (drought.series.dates ?? []).map((d, i) => ({ d, spi3: drought.series.spi3?.[i] ?? null }));

  return (
    <Panel
      title="Drought index"
      subtitle={`Month ${fmt.monthLabel(drought.target_month ?? drought.recorded_at)} · SPI through ${fmt.monthLabel(drought.spi_through)}`}
      right={<span className={`rounded-md px-2 py-1 text-xs font-bold uppercase ${style.badge}`}>{cat}</span>}
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <Stat label="SPI-3" value={fmt.num(drought.spi3, 2)} note={spiWord(drought.spi3)} />
        <Stat label="SPEI-3" value={fmt.num(drought.spei3, 2)} note={spiWord(drought.spei3)} />
        <Stat label="SPI-12" value={fmt.num(drought.spi12, 2)} note={spiWord(drought.spi12)} />
        <Stat label="VHI" value={fmt.num(drought.vhi, 0)} note={`VCI ${fmt.num(drought.vci, 0)} · TCI ${fmt.num(drought.tci, 0)}`} />
        <Stat
          label="Soil moisture"
          value={drought.soil_moisture.sm_z == null ? "—" : `z ${fmt.num(drought.soil_moisture.sm_z, 2)}`}
          note={drought.soil_moisture.sm_anomaly_pct == null ? "SMAP unavailable" : `${drought.soil_moisture.sm_anomaly_pct > 0 ? "+" : ""}${fmt.num(drought.soil_moisture.sm_anomaly_pct, 0)}% vs normal`}
        />
        <Stat
          label="Composite"
          value={fmt.num(drought.composite.score, 1)}
          note={`0 normal → 4 extreme · weights ${Object.entries(drought.composite.weights_used ?? {})
            .map(([k, w]) => `${k} ${w}`)
            .join(", ")}`}
        />
      </dl>
      {series.length > 0 && (
        <div className="mt-3 h-20 w-full">
          <ResponsiveContainer>
            <LineChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
              <YAxis domain={[-3, 3]} hide />
              <ReferenceLine y={0} stroke="#475569" />
              <ReferenceLine y={-1} stroke="#f97316" strokeDasharray="3 3" />
              <Tooltip
                contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 11 }}
                labelFormatter={(_, p) => (p?.[0]?.payload as { d?: string } | undefined)?.d ?? ""}
                formatter={(v) => [fmt.num(typeof v === "number" ? v : Number(v), 2), "SPI-3"]}
              />
              <Line type="monotone" dataKey="spi3" stroke="#38bdf8" strokeWidth={1.8} dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
          <p className="text-[10px] text-slate-500">SPI-3, last 24 months (CHIRPS)</p>
        </div>
      )}
    </Panel>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="font-semibold text-slate-100">
        {value} {note && <span className="text-[11px] font-normal text-slate-400">{note}</span>}
      </dd>
    </div>
  );
}
