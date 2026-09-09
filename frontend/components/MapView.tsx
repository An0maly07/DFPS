"use client";

import { AttributionControl, Map as MLMap, NavigationControl, ScaleControl, setWorkerUrl, type LayerSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import type { StyleSpecification } from "maplibre-gl";

// MapLibre 6 spawns its worker from `new URL('./maplibre-gl-worker.mjs', import.meta.url)`, which
// Turbopack resolves to the page URL — the worker then loads HTML and dies silently, so GeoJSON
// sources (region outlines, drought choropleth) never appear. Serve the worker + its shared chunk
// from /public instead (copied from node_modules/maplibre-gl/dist; keep in sync on upgrades).
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

// Default basemap: Esri World Light Gray canvas (keyless raster, attribution required).
// Base and reference (labels) are separate sources, so overlays sit under the labels.
// Set NEXT_PUBLIC_BASEMAP=vector to use OpenFreeMap's vector Positron style instead;
// its TileJSON sources are validated and unreachable ones dropped, otherwise MapLibre
// never fires `load`. If the vector style can't be fetched, OSM raster is the fallback.
const ESRI_ATTR = 'Tiles &copy; <a href="https://www.esri.com/">Esri</a> — Esri, HERE, Garmin, &copy; OpenStreetMap contributors';
const ESRI_LIGHT_GRAY: StyleSpecification = {
  version: 8,
  sources: {
    "esri-base": {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: 16,
      attribution: ESRI_ATTR,
    },
    "esri-ref": {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: 16,
    },
  },
  layers: [
    { id: "esri-base", type: "raster", source: "esri-base" },
    { id: "esri-ref", type: "raster", source: "esri-ref" },
  ],
};
// raster label layer to insert overlays beneath (vector styles use their first symbol layer)
const RASTER_LABEL_LAYER = "esri-ref";
const VECTOR_STYLE = process.env.NEXT_PUBLIC_BASEMAP_STYLE ?? "https://tiles.openfreemap.org/styles/positron";
const OSM_RASTER_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

async function loadBasemapStyle(): Promise<StyleSpecification> {
  if (process.env.NEXT_PUBLIC_BASEMAP !== "vector") return ESRI_LIGHT_GRAY;
  try {
    const res = await fetch(VECTOR_STYLE, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error(`style ${res.status}`);
    const style = (await res.json()) as StyleSpecification;
    const bad = new Set<string>();
    await Promise.all(
      Object.entries(style.sources).map(async ([name, src]) => {
        const url = (src as { url?: string }).url;
        if (!url) return;
        try {
          const r = await fetch(url, { signal: AbortSignal.timeout(6000) });
          if (!r.ok) bad.add(name);
        } catch {
          bad.add(name);
        }
      }),
    );
    if (bad.size) {
      console.warn("basemap: dropping unreachable sources", [...bad]);
      for (const b of bad) delete style.sources[b];
      style.layers = style.layers.filter((l) => !("source" in l) || !bad.has(l.source as string));
    }
    return style;
  } catch (e) {
    console.warn("basemap: vector style unavailable, using OSM raster", e);
    return OSM_RASTER_STYLE;
  }
}

interface MapCtx {
  map: MLMap | null;
  loaded: boolean;
  /** id of the first symbol (label) layer, so overlays can be inserted beneath labels */
  labelLayerId: string | undefined;
}

const MapContext = createContext<MapCtx>({ map: null, loaded: false, labelLayerId: undefined });
export const useMap = () => useContext(MapContext);

export interface MapViewProps {
  bounds: [number, number, number, number] | null;
  children?: ReactNode;
  className?: string;
  /** maps sharing a syncKey follow each other's camera (side-by-side compare) */
  syncKey?: string;
}

// camera sync registry: syncKey -> maps
const syncGroups = new Map<string, Set<MLMap>>();
function attachSync(map: MLMap, key: string) {
  const group = syncGroups.get(key) ?? new Set<MLMap>();
  group.add(map);
  syncGroups.set(key, group);
  let busy = false;
  const onMove = () => {
    if (busy) return;
    busy = true;
    for (const other of group) {
      if (other === map) continue;
      other.jumpTo({ center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });
    }
    busy = false;
  };
  map.on("move", onMove);
  return () => {
    map.off("move", onMove);
    group.delete(map);
  };
}

/** MapLibre GL map. Import with next/dynamic({ ssr: false }) — it needs `window`. */
export default function MapView({ bounds, children, className, syncKey }: MapViewProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const [ctx, setCtx] = useState<MapCtx>({ map: null, loaded: false, labelLayerId: undefined });

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    let cancelled = false;
    let map: MLMap | null = null;
    let detachSync: (() => void) | null = null;
    loadBasemapStyle().then((style) => {
      if (cancelled || !container.current) return;
      map = new MLMap({
        container: container.current,
        style,
        center: [74.3, 16.7],
        zoom: 8.5,
        attributionControl: false,
      });
      map.addControl(new NavigationControl({ visualizePitch: false }), "top-right");
      map.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");
      map.addControl(new AttributionControl({ compact: true }), "bottom-right");
      map.on("load", () => {
        if (!map) return;
        const layers = map.getStyle().layers ?? [];
        const labelLayerId =
          layers.find((l: LayerSpecification) => l.id === RASTER_LABEL_LAYER)?.id ??
          layers.find((l: LayerSpecification) => l.type === "symbol")?.id;
        setCtx({ map, loaded: true, labelLayerId });
      });
      map.on("error", (e) => console.error("maplibre:", e.error?.message ?? e));
      if (syncKey) detachSync = attachSync(map, syncKey);
      mapRef.current = map;
      (window as unknown as { __map?: MLMap }).__map = map; // debugging aid
    });
    return () => {
      cancelled = true;
      detachSync?.();
      map?.remove();
      mapRef.current = null;
    };
  }, [syncKey]);

  // Fit only when the bounds *values* change — every API response is a new array.
  const boundsKey = bounds ? bounds.map((v) => v.toFixed(4)).join(",") : "";
  const lastFit = useRef("");
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ctx.loaded || !bounds || boundsKey === lastFit.current) return;
    lastFit.current = boundsKey;
    map.fitBounds(
      [
        [bounds[0], bounds[1]],
        [bounds[2], bounds[3]],
      ],
      { padding: 24, duration: 800, maxZoom: 11 },
    );
  }, [bounds, boundsKey, ctx.loaded]);

  return (
    <MapContext.Provider value={ctx}>
      <div ref={container} className={className ?? "h-[520px] w-full rounded-xl overflow-hidden"} />
      {children}
    </MapContext.Provider>
  );
}
