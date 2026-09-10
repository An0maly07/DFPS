import type {
  DroughtIndex,
  EventsList,
  FloodExtent,
  Forecast,
  Health,
  LatestRisk,
  RegionsFC,
  Replay,
  RiskScoreResponse,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

/** The flood-risk model is per-basin: /api/risk-score returns 422 for a district with no
 *  forecast points, and the detail names every district it *does* cover. Returns that list
 *  (so the UI can label the gap), or null when the error is something else. */
export function floodModelCoverage(e: unknown): string[] | null {
  if (!(e instanceof ApiError) || e.status !== 422) return null;
  const detail = typeof e.detail === "string" ? e.detail : "";
  if (!detail.includes("no forecast points configured")) return null;
  const listed = detail.match(/covers:\s*\[(.*?)\]/)?.[1] ?? "";
  return listed
    .split(",")
    .map((s) => s.trim().replace(/^['"]|['"]$/g, ""))
    .filter(Boolean);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const qs = (params: Record<string, string | number | boolean | undefined>) =>
  "?" +
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");

export const api = {
  health: () => request<Health>("/health"),
  regions: () => request<RegionsFC>("/api/regions"),
  events: () => request<EventsList>("/api/events"),
  forecast: (lat: number, lon: number, days = 7, pastDays = 7) =>
    request<Forecast>(`/api/forecast${qs({ lat, lon, days, past_days: pastDays })}`),
  floodExtent: (district: string, date?: string, includeGeojson = false, source: "sar_otsu" | "prithvi" = "sar_otsu") =>
    request<FloodExtent>(`/api/flood-extent${qs({ district, date, include_geojson: includeGeojson, source })}`),
  latestRisk: (district: string) => request<LatestRisk>(`/api/risk-score/latest${qs({ district })}`),
  scoreRisk: (district: string, features?: Record<string, number>, persist = false) =>
    request<RiskScoreResponse>("/api/risk-score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ district, features, persist }),
    }),
  droughtIndex: (district: string, month?: string) =>
    request<DroughtIndex>(`/api/drought-index${qs({ district, month })}`),
  replay: (event: string) => request<Replay>(`/api/events/replay${qs({ event })}`),
};
