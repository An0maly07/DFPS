import type { AlertLevel, LatestRisk, ReplayEntry, RiskScoreResponse, RiskView, Thresholds } from "./types";
import { ALERT_LEVELS } from "./types";

export const LEVEL_STYLE: Record<AlertLevel, { bg: string; text: string; ring: string; hex: string; label: string }> = {
  green: { bg: "bg-emerald-600", text: "text-emerald-50", ring: "ring-emerald-400", hex: "#059669", label: "Low" },
  yellow: { bg: "bg-yellow-500", text: "text-yellow-950", ring: "ring-yellow-300", hex: "#eab308", label: "Watch" },
  orange: { bg: "bg-orange-600", text: "text-orange-50", ring: "ring-orange-400", hex: "#ea580c", label: "Warning" },
  red: { bg: "bg-red-700", text: "text-red-50", ring: "ring-red-400", hex: "#b91c1c", label: "Severe" },
};

export const levelRank = (l: AlertLevel) => ALERT_LEVELS.indexOf(l);

/** Human labels for model features (SHAP panel, tooltips). */
export const FEATURE_LABELS: Record<string, string> = {
  rain_mm: "Rain today (mm)",
  rain_3d: "Rain, last 3 days (mm)",
  rain_7d: "Rain, last 7 days (mm)",
  upstream_rain_mm: "Ghats rain today (mm)",
  upstream_rain_7d: "Ghats rain, 7 days (mm)",
  soil_moisture_pct: "Soil moisture (%)",
  temperature_c: "Temperature (°C)",
  discharge: "River discharge (m³/s)",
  discharge_trend: "Discharge change (m³/s/day)",
  doy_sin: "Season (sin)",
  doy_cos: "Season (cos)",
};

export function fromLatest(r: LatestRisk): RiskView {
  return {
    level: r.alert_level,
    score: r.risk_score,
    scoredFor: r.shap_json?.scored_for ?? r.scored_at.slice(0, 10),
    storedAt: r.scored_at,
    shap: r.shap_json?.values ?? {},
    baseValue: r.shap_json?.base_value ?? 0,
    features: r.shap_json?.features ?? {},
    explanation: r.explanation,
    labelSource: r.shap_json?.label_source,
    modelVersion: r.shap_json?.model_version,
    source: "stored",
  };
}

export function fromFresh(r: RiskScoreResponse): RiskView {
  return {
    level: r.alert_level,
    score: r.risk_score,
    scoredFor: r.context.scored_for ?? new Date().toISOString().slice(0, 10),
    storedAt: r.persisted?.scored_at,
    shap: r.shap_values,
    baseValue: r.shap_base_value,
    features: r.features,
    explanation: r.explanation,
    labelSource: r.label_source,
    modelVersion: r.model_version,
    source: "fresh",
  };
}

export function fromReplay(e: ReplayEntry): RiskView {
  const entries = Object.entries(e.shap_values).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  return {
    level: e.alert_level,
    score: e.risk_score,
    scoredFor: e.date,
    shap: e.shap_values,
    baseValue: 0,
    features: {
      rain_mm: e.rain_mm,
      rain_3d: e.rain_3d,
      upstream_rain_7d: e.upstream_rain_7d,
      soil_moisture_pct: e.soil_moisture_pct,
      discharge: e.discharge,
    },
    explanation: {
      pushing_up: entries.filter(([, v]) => v > 0).slice(0, 3).map(([feature, contribution]) => ({ feature, contribution })),
      pushing_down: entries.filter(([, v]) => v < 0).slice(0, 3).map(([feature, contribution]) => ({ feature, contribution })),
    },
    source: "replay",
  };
}

export interface RiverStatus {
  label: string;
  tone: AlertLevel;
  discharge: number;
  ratio: number; // discharge / q_2yr
}

/** Rule-based "what the river is doing now" — a threshold display, not a model. */
export function riverStatus(discharge: number | null | undefined, t: Thresholds | null | undefined): RiverStatus | null {
  if (discharge == null || !t) return null;
  const ratio = discharge / t.q_2yr;
  if (discharge >= t.q_5yr) return { label: "Major flood flow (≥ 5-yr)", tone: "red", discharge, ratio };
  if (discharge >= t.q_2yr) return { label: "Flood flow (≥ 2-yr)", tone: "orange", discharge, ratio };
  if (discharge >= t.q_1_5yr) return { label: "Bankfull (≥ 1.5-yr)", tone: "yellow", discharge, ratio };
  if (discharge >= 0.5 * t.q_1_5yr) return { label: "Elevated", tone: "yellow", discharge, ratio };
  return { label: "Normal", tone: "green", discharge, ratio };
}

export const fmt = {
  num: (v: number | null | undefined, d = 1) => (v == null || Number.isNaN(v) ? "—" : v.toFixed(d)),
  pct: (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(v < 0.01 ? 2 : 0)}%`),
  date: (s: string | null | undefined) =>
    s ? new Date(s.length === 10 ? `${s}T00:00:00Z` : s).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }) : "—",
  monthLabel: (ym: string | null | undefined) =>
    ym ? new Date(`${ym.slice(0, 7)}-01T00:00:00Z`).toLocaleDateString("en-IN", { month: "short", year: "numeric", timeZone: "UTC" }) : "—",
};

export const DROUGHT_STYLE: Record<string, { hex: string; badge: string }> = {
  normal: { hex: "#22c55e", badge: "bg-emerald-600 text-emerald-50" },
  mild: { hex: "#facc15", badge: "bg-yellow-500 text-yellow-950" },
  moderate: { hex: "#f97316", badge: "bg-orange-500 text-orange-50" },
  severe: { hex: "#dc2626", badge: "bg-red-600 text-red-50" },
  extreme: { hex: "#7f1d1d", badge: "bg-red-900 text-red-50" },
};
