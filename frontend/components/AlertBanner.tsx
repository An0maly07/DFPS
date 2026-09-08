"use client";

import { LEVEL_STYLE, fmt } from "@/lib/risk";
import type { RiskView } from "@/lib/types";
import type { RiverStatus } from "@/lib/risk";

interface Props {
  risk: RiskView | null;
  river: RiverStatus | null;
  district: string;
  mode: "live" | "replay";
  loading?: boolean;
  error?: string | null;
  onScoreNow?: () => void;
  scoring?: boolean;
}

export default function AlertBanner({ risk, river, district, mode, loading, error, onScoreNow, scoring }: Props) {
  const level = risk?.level ?? "green";
  const s = LEVEL_STYLE[level];
  const riverStyle = river ? LEVEL_STYLE[river.tone] : null;

  return (
    <div className={`${risk ? s.bg : "bg-slate-700"} ${risk ? s.text : "text-slate-100"} px-5 py-4 shadow-lg`}>
      <div className="mx-auto max-w-7xl flex flex-wrap items-center gap-x-8 gap-y-2">
        <div className="flex items-baseline gap-3">
          <span className="text-xs uppercase tracking-widest opacity-80">Flood alert · {district}</span>
          {risk ? (
            <span className="text-3xl font-bold leading-none">
              {level.toUpperCase()} <span className="text-base font-medium opacity-80">({s.label})</span>
            </span>
          ) : (
            <span className="text-xl font-semibold">{loading ? "Loading…" : error ?? "No score yet"}</span>
          )}
        </div>
        {risk && (
          <div className="text-sm leading-tight">
            <div>
              Risk of flood flow in next 3 days: <b>{fmt.pct(risk.score)}</b>
            </div>
            <div className="opacity-85">
              {mode === "replay" ? "Replay day" : "Scored for"} {fmt.date(risk.scoredFor)}
              {risk.storedAt && mode === "live" ? ` · stored ${new Date(risk.storedAt).toLocaleTimeString("en-IN")}` : ""}
            </div>
          </div>
        )}
        {river && riverStyle && (
          <div className="text-sm leading-tight">
            <div className="flex items-center gap-2">
              <span className={`inline-block h-2.5 w-2.5 rounded-full ${riverStyle.bg} ring-2 ring-white/60`} />
              River now: <b>{river.label}</b>
            </div>
            <div className="opacity-85">
              {fmt.num(river.discharge, 0)} m³/s · {fmt.num(river.ratio * 100, 0)}% of 2-yr flow
            </div>
          </div>
        )}
        <div className="ml-auto flex items-center gap-3 text-xs">
          {risk?.labelSource && <span className="opacity-75">model {risk.modelVersion} · labels: proxy (GloFAS)</span>}
          {mode === "live" && onScoreNow && (
            <button
              onClick={onScoreNow}
              disabled={scoring}
              className="rounded-md bg-black/25 px-3 py-1.5 font-semibold hover:bg-black/40 disabled:opacity-50"
            >
              {scoring ? "Scoring…" : "Score now"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
