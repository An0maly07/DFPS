"use client";

import type { GeoJSONSource } from "maplibre-gl";
import { useEffect } from "react";
import { useMap } from "./MapView";
import { DROUGHT_STYLE } from "@/lib/risk";
import type { RegionsFC } from "@/lib/types";

interface Props {
  regions: RegionsFC | null;
  selected: string;
  /** composite drought category for the selected region (fills its polygon) */
  category: string | null;
  visible: boolean;
}

const SRC = "regions";

/** Region outlines for every district + a drought-category choropleth fill for the selected one. */
export default function DroughtLayer({ regions, selected, category, visible }: Props) {
  const { map, loaded, labelLayerId } = useMap();

  useEffect(() => {
    if (!map || !loaded || !regions) return;
    const fc = {
      ...regions,
      features: regions.features.map((f) => ({
        ...f,
        properties: {
          ...f.properties,
          selected: f.properties.name === selected,
          fill: f.properties.name === selected && category ? DROUGHT_STYLE[category]?.hex ?? "#999" : "#000000",
          fillOpacity: f.properties.name === selected && category && visible ? 0.28 : 0,
        },
      })),
    };
    const src = map.getSource(SRC) as GeoJSONSource | undefined;
    if (src) {
      src.setData(fc as GeoJSON.FeatureCollection);
      return;
    }
    map.addSource(SRC, { type: "geojson", data: fc as GeoJSON.FeatureCollection });
    map.addLayer(
      {
        id: "regions-fill",
        type: "fill",
        source: SRC,
        paint: { "fill-color": ["get", "fill"], "fill-opacity": ["get", "fillOpacity"] },
      },
      labelLayerId,
    );
    map.addLayer(
      {
        id: "regions-line",
        type: "line",
        source: SRC,
        paint: {
          "line-color": ["case", ["get", "selected"], "#0ea5e9", "#64748b"],
          "line-width": ["case", ["get", "selected"], 2.5, 1],
          "line-dasharray": [2, 1],
        },
      },
      labelLayerId,
    );
  }, [map, loaded, labelLayerId, regions, selected, category, visible]);

  return null;
}
