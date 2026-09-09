"use client";

import dynamic from "next/dynamic";
import FloodOverlay from "./FloodOverlay";
import { fmt } from "@/lib/risk";
import type { FloodExtent, TileSet } from "@/lib/types";

const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => <div className="h-[520px] w-full animate-pulse rounded-xl bg-slate-800" />,
});

interface Props {
  /** SAR side (already loaded for the main view) */
  sarTiles: TileSet | null;
  sarDate: string | null;
  sarAreaKm2: number | null;
  /** Prithvi side */
  prithvi: FloodExtent | null;
  prithviError: string | null;
  prithviLoading: boolean;
  bounds: [number, number, number, number] | null;
  showMask: boolean;
}

/**
 * Side-by-side SAR-vs-Prithvi comparison (PLAN.md §3.2): two camera-synced maps.
 * Left: Sentinel-1 backscatter + edge-Otsu mask. Right: Sentinel-2 true colour + Prithvi mask,
 * with clouds rendered grey — the "optical model vs SAR under cloud cover" moment.
 */
export default function CompareView({ sarTiles, sarDate, sarAreaKm2, prithvi, prithviError, prithviLoading, bounds, showMask }: Props) {
  const pane = "h-[520px] w-full rounded-xl overflow-hidden";
  const cap = "pointer-events-none absolute left-2 top-2 z-10 rounded-md bg-slate-950/80 px-2.5 py-1.5 text-[11px] leading-snug text-slate-100";
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <div className="relative">
        <div className={cap}>
          <b>Sentinel-1 SAR + edge-Otsu</b>
          <br />
          {sarDate ? `${fmt.date(sarDate)} · ${fmt.num(sarAreaKm2, 1)} km² open water · sees through cloud` : "no SAR scene"}
        </div>
        <MapView bounds={bounds} syncKey="compare" className={pane}>
          <FloodOverlay tiles={sarTiles} bounds={bounds} showMask={showMask} showSar sarImage="vv_post" source="sar_otsu" />
        </MapView>
      </div>
      <div className="relative">
        <div className={cap}>
          <b>Sentinel-2 optical + Prithvi-EO</b>
          <br />
          {prithvi
            ? `${fmt.date(prithvi.captured_at)} · ${fmt.num(prithvi.flood_area_km2, 1)} km² flood · ${fmt.num(prithvi.model?.cloud_pct ?? null, 0)} % cloud (grey = not assessable)`
            : prithviLoading
              ? "loading Prithvi extent…"
              : prithviError ?? "no Prithvi extent"}
        </div>
        <MapView bounds={bounds} syncKey="compare" className={pane}>
          <FloodOverlay tiles={prithvi?.tiles ?? null} bounds={prithvi?.bounds ?? bounds} showMask={showMask} showSar sarImage="vv_post" source="prithvi" />
        </MapView>
      </div>
    </div>
  );
}
