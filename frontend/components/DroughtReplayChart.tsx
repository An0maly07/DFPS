"use client";

import { CartesianGrid, ComposedChart, Legend, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Panel } from "./ShapPanel";
import { DROUGHT_STYLE, fmt } from "@/lib/risk";
import type { DroughtReplay } from "@/lib/types";

interface Props {
  replay: DroughtReplay;
  cursorMonth: string | null;
  onPick?: (month: string) => void;
  /** e.g. the government drought declaration, YYYY-MM */
  declaredMonth?: string;
}

/** Monthly SPI-3 / SPEI-3 / VHI / SMAP z-score through a drought event, shaded by composite category. */
export default function DroughtReplayChart({ replay, cursorMonth, onPick, declaredMonth }: Props) {
  const data = replay.timeline.map((t) => ({
    month: t.month,
    label: fmt.monthLabel(t.month),
    spi3: t.spi3,
    spei3: t.spei3,
    sm_z: t.sm_z,
    vhi: t.vhi,
    cat: t.composite_category,
  }));
  return (
    <Panel
      title={`Replay · ${replay.title}`}
      subtitle="CHIRPS SPI-3 · ERA5-Land SPEI-3 · SMAP root-zone z-score (left axis) · MODIS VHI (right axis, <40 = vegetation stress) · shading = composite category"
    >
      <div className="h-72 w-full">
        <ResponsiveContainer>
          <ComposedChart
            data={data}
            margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
            onClick={(e) => {
              const lbl = (e as { activeLabel?: string | number } | null)?.activeLabel;
              const pt = lbl != null ? data.find((p) => p.label === String(lbl)) : undefined;
              if (pt && onPick) onPick(pt.month);
            }}
          >
            <CartesianGrid stroke="#334155" strokeDasharray="2 4" />
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#94a3b8" }} />
            <YAxis yAxisId="z" domain={[-4, 2]} ticks={[-4, -3, -2, -1, 0, 1, 2]} tick={{ fontSize: 11, fill: "#94a3b8" }} width={40} />
            <YAxis yAxisId="vhi" orientation="right" domain={[0, 100]} tick={{ fontSize: 11, fill: "#94a3b8" }} width={40} />
            <Tooltip
              contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }}
              formatter={(v, name) => [fmt.num(typeof v === "number" ? v : Number(v), name === "VHI" ? 0 : 2), name]}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {data.map((p) =>
              p.cat !== "normal" ? (
                <ReferenceArea
                  key={p.month}
                  yAxisId="z"
                  x1={p.label}
                  x2={p.label}
                  fill={(DROUGHT_STYLE[p.cat] ?? DROUGHT_STYLE.normal).hex}
                  fillOpacity={0.22}
                  ifOverflow="extendDomain"
                />
              ) : null,
            )}
            <ReferenceLine yAxisId="z" y={0} stroke="#64748b" />
            <ReferenceLine yAxisId="z" y={-1} stroke="#f97316" strokeDasharray="4 4" label={{ value: "moderate", fill: "#f97316", fontSize: 10, position: "insideBottomRight" }} />
            <ReferenceLine yAxisId="z" y={-2} stroke="#ef4444" strokeDasharray="4 4" label={{ value: "extreme", fill: "#ef4444", fontSize: 10, position: "insideBottomRight" }} />
            <Line yAxisId="z" dataKey="spi3" name="SPI-3" stroke="#38bdf8" strokeWidth={2.2} dot={{ r: 3 }} connectNulls />
            <Line yAxisId="z" dataKey="spei3" name="SPEI-3" stroke="#a78bfa" strokeWidth={1.8} dot={false} connectNulls />
            <Line yAxisId="z" dataKey="sm_z" name="Soil moisture z" stroke="#fbbf24" strokeWidth={1.5} strokeDasharray="5 3" dot={false} connectNulls />
            <Line yAxisId="vhi" dataKey="vhi" name="VHI" stroke="#4ade80" strokeWidth={1.8} dot={false} connectNulls />
            {cursorMonth && <ReferenceLine yAxisId="z" x={fmt.monthLabel(cursorMonth)} stroke="#e2e8f0" strokeWidth={2} />}
            {declaredMonth && (
              <ReferenceLine yAxisId="z" x={fmt.monthLabel(declaredMonth)} stroke="#f87171" strokeDasharray="2 2" label={{ value: "drought declared", fill: "#f87171", fontSize: 10, position: "insideTopLeft" }} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}
