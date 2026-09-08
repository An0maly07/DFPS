"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import AlertBanner from "@/components/AlertBanner";
import DroughtCard from "@/components/DroughtCard";
import DroughtLayer from "@/components/DroughtLayer";
import FloodOverlay from "@/components/FloodOverlay";
import ForecastChart from "@/components/ForecastChart";
import LayerToggles from "@/components/LayerToggles";
import ReplayEventSelector from "@/components/ReplayEventSelector";
import ShapPanel from "@/components/ShapPanel";
import TimeSlider from "@/components/TimeSlider";
import { ApiError, API_URL, api } from "@/lib/apiClient";
import { fmt, fromFresh, fromLatest, fromReplay, riverStatus } from "@/lib/risk";
import type { DroughtIndex, EventsList, FloodExtent, Forecast, Health, RegionsFC, Replay, RiskView, TileSet } from "@/lib/types";

// MapLibre needs `window` — never render it on the server.
const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => <div className="h-[520px] w-full animate-pulse rounded-xl bg-slate-800" />,
});

const POLL_MS = 30_000;
const NO_DATES: string[] = [];
const errText = (e: unknown) =>
  e instanceof ApiError ? `${e.status}: ${typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}` : String(e);

/** Data tagged with the selection it belongs to, so stale results are ignored without resets. */
type Tagged<T> = { key: string; data: T | null; error: string | null };
const tagged = <T,>(key: string, data: T | null, error: string | null = null): Tagged<T> => ({ key, data, error });
const pick = <T,>(t: Tagged<T> | null, key: string): { data: T | null; error: string | null; loading: boolean } =>
  t && t.key === key ? { data: t.data, error: t.error, loading: false } : { data: null, error: null, loading: true };

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [regions, setRegions] = useState<RegionsFC | null>(null);
  const [events, setEvents] = useState<EventsList["events"]>([]);
  const [district, setDistrict] = useState("kolhapur-sangli");
  const [mode, setMode] = useState("live"); // "live" | event id

  const [forecast, setForecast] = useState<Tagged<Forecast> | null>(null);
  const [risk, setRisk] = useState<Tagged<RiskView> | null>(null);
  const [scoring, setScoring] = useState(false);
  const [extent, setExtent] = useState<Tagged<FloodExtent> | null>(null);
  const [dateIdx, setDateIdx] = useState<{ key: string; i: number } | null>(null);
  const [drought, setDrought] = useState<Tagged<DroughtIndex> | null>(null);
  const [replay, setReplay] = useState<Tagged<Replay> | null>(null);
  const [replayIdx, setReplayIdx] = useState(0);
  const [layers, setLayers] = useState({ showMask: true, showSar: false, sarImage: "vv_post" as "vv_post" | "vv_pre", showDrought: true });

  const isReplay = mode !== "live";
  const region = regions?.features.find((f) => f.properties.name === district)?.properties;
  const bounds = region?.bounds ?? null;

  // --- bootstrap ---------------------------------------------------------------------
  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.regions().then(setRegions).catch(() => setRegions(null));
    api.events().then((e) => setEvents(e.events)).catch(() => setEvents([]));
  }, []);

  // --- live: forecast, latest risk, drought, flood extent ---------------------------------
  useEffect(() => {
    if (!region || isReplay) return;
    const d = district;
    const lat = region.lat ?? 16.7;
    const lon = region.lon ?? 74.24;
    api.forecast(lat, lon, 7, 7).then((f) => setForecast(tagged(d, f))).catch((e) => setForecast(tagged<Forecast>(d, null, errText(e))));
    api
      .latestRisk(d)
      .then((r) => setRisk(tagged(d, fromLatest(r))))
      .catch((e) => setRisk(tagged<RiskView>(d, null, e instanceof ApiError && e.status === 404 ? "No stored score yet — press Score now" : errText(e))));
    api.droughtIndex(d).then((x) => setDrought(tagged(d, x))).catch((e) => setDrought(tagged<DroughtIndex>(d, null, errText(e))));
    api
      .floodExtent(d, "peak") // open on the peak-extent scene; the slider still reaches every date
      .then((fe) => {
        setExtent(tagged(d, fe));
        setDateIdx({ key: d, i: Math.max(0, fe.available_dates.indexOf(fe.captured_at)) });
      })
      .catch((e) => setExtent(tagged<FloodExtent>(d, null, e instanceof ApiError && e.status === 404 ? "No SAR flood extents for this district" : errText(e))));
  }, [region, district, isReplay]);

  // poll the stored score (PLAN.md §6.2)
  useEffect(() => {
    if (isReplay) return;
    const d = district;
    const id = setInterval(() => {
      api.latestRisk(d).then((r) => setRisk(tagged(d, fromLatest(r)))).catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(id);
  }, [district, isReplay]);

  // time-slider → swap COG (debounced)
  const extentView = pick(extent, district);
  const dates = extentView.data?.available_dates ?? NO_DATES;
  const curDateIdx = dateIdx?.key === district ? dateIdx.i : Math.max(0, dates.length - 1);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (isReplay || dates.length === 0) return;
    const d = district;
    const want = dates[curDateIdx];
    if (!want || want === extentView.data?.captured_at) return;
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      api.floodExtent(d, want).then((fe) => setExtent(tagged(d, fe))).catch((e) => setExtent(tagged<FloodExtent>(d, null, errText(e))));
    }, 300);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [curDateIdx, dates, district, isReplay, extentView.data?.captured_at]);

  const scoreNow = () => {
    const d = district;
    setScoring(true);
    api
      .scoreRisk(d, undefined, true)
      .then((r) => setRisk(tagged(d, fromFresh(r))))
      .catch((e) => setRisk(tagged<RiskView>(d, null, errText(e))))
      .finally(() => setScoring(false));
  };

  // --- replay ------------------------------------------------------------------------------
  useEffect(() => {
    if (!isReplay) return;
    const ev = mode;
    api
      .replay(ev)
      .then((r) => {
        setReplay(tagged(ev, r));
        setDistrict(r.region);
        const anchor = r.summary.first_orange ?? r.documented_peak ?? r.start;
        const i = r.timeline.findIndex((t) => t.date === anchor);
        setReplayIdx(i >= 0 ? Math.max(0, i - 4) : 0);
        // thresholds for the chart's return-period lines
        api.forecast(16.7, 74.24, 1, 1).then((f) => setForecast(tagged(r.region, f))).catch(() => undefined);
        api.droughtIndex(r.region).then((x) => setDrought(tagged(r.region, x))).catch((e) => setDrought(tagged<DroughtIndex>(r.region, null, errText(e))));
      })
      .catch((e) => setReplay(tagged<Replay>(ev, null, errText(e))));
  }, [mode, isReplay]);

  const replayView = pick(replay, mode);
  const rp = replayView.data;
  const replayEntry = rp?.timeline[Math.min(replayIdx, (rp?.timeline.length ?? 1) - 1)] ?? null;
  // most recent SAR snapshot on or before the selected replay day
  let replayExtent: (Replay["timeline"][number]["flood_extent"] & { date: string }) | null = null;
  if (rp) {
    for (let i = Math.min(replayIdx, rp.timeline.length - 1); i >= 0; i--) {
      const fe = rp.timeline[i].flood_extent;
      if (fe) {
        replayExtent = { ...fe, date: rp.timeline[i].date };
        break;
      }
    }
  }

  // --- derived view state ----------------------------------------------------------------------
  const forecastView = pick(forecast, district);
  const riskLive = pick(risk, district);
  const droughtView = pick(drought, district);
  const riskView: RiskView | null = isReplay ? (replayEntry ? fromReplay(replayEntry) : null) : riskLive.data;
  const tiles: TileSet | null = isReplay ? replayExtent?.tiles ?? null : extentView.data?.tiles ?? null;
  const tileBounds = (isReplay ? replayExtent?.bounds : extentView.data?.bounds) ?? bounds;
  const thresholds = forecastView.data?.thresholds ?? null;
  const fc = forecastView.data;
  const todayQ = fc ? fc.discharge.river_discharge[fc.discharge.dates.indexOf(fc.today)] : null;
  const river = isReplay ? riverStatus(replayEntry?.discharge, thresholds) : riverStatus(todayQ, thresholds);
  const extentLabel = isReplay
    ? replayExtent
      ? `SAR extent ${fmt.date(replayExtent.date)} · ${fmt.num(replayExtent.flood_area_km2, 1)} km² open water`
      : "No SAR scene yet for this day"
    : extentView.data
      ? `SAR extent ${fmt.date(extentView.data.captured_at)} · ${fmt.num(extentView.data.flood_area_km2, 1)} km² open water · Otsu ${fmt.num(extentView.data.thresholds_db.post, 1)} dB`
      : extentView.error;

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-950/80 px-5 py-3">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4">
          <div>
            <h1 className="text-lg font-bold tracking-tight">
              Indradhanu <span className="font-normal text-slate-400">· Drought &amp; Flood Risk</span>
            </h1>
            <p className="text-[11px] text-slate-500">Sentinel-1 SAR · GloFAS · XGBoost + SHAP · SPI/VHI/SMAP</p>
          </div>
          <label className="ml-auto flex items-center gap-2 text-xs text-slate-300">
            <span className="uppercase tracking-wide text-slate-400">District</span>
            <select
              value={district}
              disabled={isReplay}
              onChange={(e) => setDistrict(e.target.value)}
              className="rounded-md border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm text-slate-100 disabled:opacity-60"
            >
              {(regions?.features ?? []).map((f) => (
                <option key={f.id} value={f.properties.name}>
                  {f.properties.name}
                </option>
              ))}
            </select>
          </label>
          <ReplayEventSelector events={events} value={mode} onChange={setMode} />
          <span className="flex items-center gap-1.5 text-[11px] text-slate-400" title={API_URL}>
            <span className={`h-2 w-2 rounded-full ${health ? "bg-emerald-400" : "bg-red-500"}`} />
            API {health ? "online" : "offline"}
          </span>
        </div>
      </header>

      <AlertBanner
        risk={riskView}
        river={river}
        district={district}
        mode={isReplay ? "replay" : "live"}
        error={isReplay ? replayView.error : riskLive.error}
        loading={isReplay ? replayView.loading : riskLive.loading}
        onScoreNow={scoreNow}
        scoring={scoring}
      />

      {isReplay && rp && (
        <div className="mx-auto max-w-7xl px-4 pt-4">
          <div className="rounded-xl border border-amber-700/50 bg-amber-950/40 px-4 py-3 text-sm text-amber-100">
            <b>{rp.title}.</b> {rp.narrative}{" "}
            {rp.type === "flood" && rp.summary.first_orange && (
              <span>
                Model first went <b>orange on {fmt.date(rp.summary.first_orange)}</b>, {rp.summary.lead_days_orange_before_peak} days before the GloFAS peak on{" "}
                {fmt.date(rp.summary.glofas_peak_date)} (documented peak {fmt.date(rp.documented_peak)}).
              </span>
            )}
          </div>
        </div>
      )}

      <main className="mx-auto grid max-w-7xl grid-cols-1 gap-4 p-4 lg:grid-cols-3">
        <section className="space-y-3 lg:col-span-2">
          <MapView bounds={tileBounds}>
            <DroughtLayer regions={regions} selected={district} category={droughtView.data?.composite.category ?? null} visible={layers.showDrought} />
            <FloodOverlay tiles={tiles} bounds={tileBounds} showMask={layers.showMask} showSar={layers.showSar} sarImage={layers.sarImage} />
          </MapView>
          <LayerToggles {...layers} onChange={(p) => setLayers((l) => ({ ...l, ...p }))} extentLabel={extentLabel} />
          {isReplay && rp ? (
            <TimeSlider title="Replay day" dates={rp.timeline.map((t) => t.date)} index={replayIdx} onChange={setReplayIdx} marks={rp.summary.sar_dates ?? []} autoplay />
          ) : (
            <TimeSlider title="Sentinel-1 scene" dates={dates} index={curDateIdx} onChange={(i) => setDateIdx({ key: district, i })} marks={dates} />
          )}
        </section>

        <aside className="space-y-4">
          <ShapPanel risk={riskView} />
          <DroughtCard drought={droughtView.data} error={droughtView.error} loading={droughtView.loading} />
        </aside>

        <section className="lg:col-span-3">
          {isReplay && rp ? (
            <ForecastChart
              mode="replay"
              replay={rp}
              thresholds={thresholds}
              cursorDate={replayEntry?.date ?? null}
              onPick={(d) => setReplayIdx(Math.max(0, rp.timeline.findIndex((t) => t.date === d)))}
            />
          ) : (
            <ForecastChart mode="live" forecast={forecastView.data} />
          )}
        </section>
      </main>

      <footer className="mx-auto max-w-7xl px-4 pb-6 text-[11px] leading-relaxed text-slate-500">
        Flood extent = Sentinel-1 VV open water via edge-Otsu (JRC permanent water and slopes &gt; 5° removed); water under vegetation or roofs is not
        detected. Risk model labels are a proxy (GloFAS discharge ≥ 2-yr flow within 3 days), not observed floods — see /api/risk-score/model. Forecasts:
        Open-Meteo / GloFAS v4. Drought: CHIRPS, ERA5-Land, MODIS, SMAP via Google Earth Engine.
      </footer>
    </div>
  );
}
