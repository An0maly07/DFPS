"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Panel } from "./ShapPanel";
import { LEVEL_STYLE, fmt } from "@/lib/risk";
import type { FloodReplay, Forecast, Thresholds } from "@/lib/types";

interface LiveProps {
  mode: "live";
  forecast: Forecast | null;
}
interface ReplayProps {
  mode: "replay";
  replay: FloodReplay;
  thresholds: Thresholds | null;
  cursorDate: string | null;
  onPick?: (date: string) => void;
}
type Props = LiveProps | ReplayProps;

interface Point {
  date: string;
  label: string;
  rain: number | null;
  discharge: number | null;
  dmin?: number | null;
  dmax?: number | null;
  risk?: number;
  level?: string;
  sar?: number | null;
}

const short = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });

export default function ForecastChart(props: Props) {
  const thresholds = props.mode === "live" ? props.forecast?.thresholds : props.thresholds;

  let data: Point[] = [];
  let today: string | null = null;
  if (props.mode === "live" && props.forecast) {
    const f = props.forecast;
    const rainByDate = new Map(f.weather.dates.map((d, i) => [d, f.weather.precipitation_mm[i]]));
    data = f.discharge.dates.map((d, i) => ({
      date: d,
      label: short(d),
      rain: rainByDate.get(d) ?? null,
      discharge: f.discharge.river_discharge[i],
      dmin: f.discharge.ensemble_min[i],
      dmax: f.discharge.ensemble_max[i],
    }));
    today = f.today;
  } else if (props.mode === "replay") {
    data = props.replay.timeline.map((t) => ({
      date: t.date,
      label: short(t.date),
      rain: t.rain_mm,
      discharge: t.discharge,
      risk: Math.round(t.risk_score * 1000) / 10,
      level: t.alert_level,
      sar: t.flood_extent?.flood_area_km2 ?? null,
    }));
  }

  const title = props.mode === "live" ? "7-day forecast · rainfall & GloFAS discharge" : `Replay · ${props.replay.title}`;
  const subtitle =
    props.mode === "live"
      ? "Open-Meteo (ERA5/ICON) rainfall · GloFAS v4 discharge · dashed lines = return-period flows from the reanalysis"
      : "ERA5 rainfall · GloFAS reanalysis discharge · model risk each day · ▲ = Sentinel-1 flood-extent snapshot";

  const maxQ = Math.max(...data.map((p) => p.discharge ?? 0), thresholds?.q_5yr ?? 0) * 1.1 || 100;

  return (
    <Panel title={title} subtitle={subtitle}>
      {data.length === 0 ? (
        <p className="text-sm text-slate-400">No data.</p>
      ) : (
        <div className="h-72 w-full">
          <ResponsiveContainer>
            <ComposedChart
              data={data}
              margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
              onClick={(e) => {
                const lbl = (e as { activeLabel?: string | number } | null)?.activeLabel;
                if (props.mode === "replay" && props.onPick && lbl != null) {
                  const pt = data.find((p) => p.label === String(lbl));
                  if (pt) props.onPick(pt.date);
                }
              }}
            >
              <CartesianGrid stroke="#334155" strokeDasharray="2 4" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#94a3b8" }} interval="preserveStartEnd" minTickGap={24} />
              <YAxis yAxisId="rain" orientation="left" tick={{ fontSize: 11, fill: "#94a3b8" }} unit=" mm" width={56} />
              <YAxis yAxisId="q" orientation="right" domain={[0, Math.ceil(maxQ / 100) * 100]} tick={{ fontSize: 11, fill: "#94a3b8" }} width={56} />
              {props.mode === "replay" && <YAxis yAxisId="risk" orientation="right" domain={[0, 100]} hide />}
              <Tooltip
                contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }}
                formatter={(v, name) => {
                  const n = typeof v === "number" ? v : Number(v);
                  if (name === "Rain") return [`${fmt.num(n, 1)} mm`, name];
                  if (name === "Risk") return [`${fmt.num(n, 1)} %`, name];
                  if (name === "SAR flood") return [`${fmt.num(n, 1)} km²`, name];
                  return [`${fmt.num(n, 0)} m³/s`, name];
                }}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {props.mode === "replay" &&
                data.map((p) =>
                  p.level && p.level !== "green" ? (
                    <ReferenceArea
                      key={p.date}
                      yAxisId="q"
                      x1={p.label}
                      x2={p.label}
                      fill={LEVEL_STYLE[p.level as keyof typeof LEVEL_STYLE].hex}
                      fillOpacity={0.18}
                      ifOverflow="extendDomain"
                    />
                  ) : null,
                )}
              <Bar yAxisId="rain" dataKey="rain" name="Rain" fill="#38bdf8" radius={[2, 2, 0, 0]} />
              {props.mode === "live" && (
                <Line yAxisId="q" dataKey="dmax" name="Ensemble max" stroke="#a78bfa" strokeWidth={1} dot={false} strokeDasharray="3 3" />
              )}
              <Line yAxisId="q" dataKey="discharge" name="Discharge" stroke="#f472b6" strokeWidth={2.2} dot={false} />
              {props.mode === "replay" && (
                <Line yAxisId="risk" dataKey="risk" name="Risk" stroke="#fbbf24" strokeWidth={2} dot={false} />
              )}
              {props.mode === "replay" && (
                <Line
                  yAxisId="q"
                  dataKey={(p: Point) => (p.sar != null ? (p.discharge ?? 0) : null)}
                  name="SAR flood"
                  stroke="transparent"
                  dot={{ r: 6, fill: "#ef4444", stroke: "#fff", strokeWidth: 1 }}
                  activeDot={false}
                  connectNulls={false}
                  legendType="triangle"
                />
              )}
              {thresholds && (
                <>
                  <ReferenceLine yAxisId="q" y={thresholds.q_1_5yr} stroke="#facc15" strokeDasharray="4 4" label={{ value: "1.5-yr", fill: "#facc15", fontSize: 10, position: "insideTopRight" }} />
                  <ReferenceLine yAxisId="q" y={thresholds.q_2yr} stroke="#fb923c" strokeDasharray="4 4" label={{ value: "2-yr", fill: "#fb923c", fontSize: 10, position: "insideTopRight" }} />
                  <ReferenceLine yAxisId="q" y={thresholds.q_5yr} stroke="#ef4444" strokeDasharray="4 4" label={{ value: "5-yr", fill: "#ef4444", fontSize: 10, position: "insideTopRight" }} />
                </>
              )}
              {today && <ReferenceLine yAxisId="q" x={short(today)} stroke="#e2e8f0" label={{ value: "today", fill: "#e2e8f0", fontSize: 10, position: "top" }} />}
              {props.mode === "replay" && props.cursorDate && (
                <ReferenceLine yAxisId="q" x={short(props.cursorDate)} stroke="#e2e8f0" strokeWidth={2} />
              )}
              {props.mode === "replay" && props.replay.documented_peak && (
                <ReferenceLine yAxisId="q" x={short(props.replay.documented_peak)} stroke="#f87171" strokeDasharray="2 2" label={{ value: "documented peak", fill: "#f87171", fontSize: 10, position: "top" }} />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
