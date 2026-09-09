"use client";

import { useEffect } from "react";
import { useMap } from "./MapView";
import type { TileSet } from "@/lib/types";

interface Props {
  tiles: TileSet | null;
  bounds: [number, number, number, number] | null;
  showMask: boolean;
  /** show the sensor image beneath the mask (SAR backscatter, or the S2 true-colour scene for Prithvi) */
  showSar: boolean;
  /** which backscatter image to show under the mask */
  sarImage: "vv_post" | "vv_pre";
  maskOpacity?: number;
  source?: "sar_otsu" | "prithvi";
}

const MASK_ID = "flood-mask";
const SAR_ID = "flood-sar";

/**
 * TiTiler raster layers for the flood COGs. The tile URL templates come straight
 * from /api/flood-extent (or the replay timeline) — nothing is composed client-side.
 */
export default function FloodOverlay({ tiles, bounds, showMask, showSar, sarImage, maskOpacity = 0.85, source = "sar_otsu" }: Props) {
  const { map, loaded, labelLayerId } = useMap();

  // Sensor image beneath the class mask: SAR backscatter (grey) or S2 true colour
  useEffect(() => {
    if (!map || !loaded) return;
    if (map.getLayer(SAR_ID)) map.removeLayer(SAR_ID);
    if (map.getSource(SAR_ID)) map.removeSource(SAR_ID);
    const url = source === "prithvi" ? tiles?.s2_rgb : tiles?.[sarImage];
    if (!showSar || !url) return;
    map.addSource(SAR_ID, {
      type: "raster",
      tiles: [url],
      tileSize: 256,
      minzoom: 7,
      maxzoom: 14,
      ...(bounds ? { bounds } : {}),
      attribution: source === "prithvi" ? "Sentinel-2 © ESA/Copernicus" : "Sentinel-1 © ESA/Copernicus",
    });
    map.addLayer({ id: SAR_ID, type: "raster", source: SAR_ID, paint: { "raster-opacity": 0.95 } }, labelLayerId);
  }, [map, loaded, labelLayerId, tiles, sarImage, showSar, bounds, source]);

  // Flood class mask (red = flood, blue = permanent/pre-event water, grey = cloud for Prithvi)
  useEffect(() => {
    if (!map || !loaded) return;
    if (map.getLayer(MASK_ID)) map.removeLayer(MASK_ID);
    if (map.getSource(MASK_ID)) map.removeSource(MASK_ID);
    const url = tiles?.mask;
    if (!showMask || !url) return;
    map.addSource(MASK_ID, {
      type: "raster",
      tiles: [url],
      tileSize: 256,
      minzoom: 7,
      maxzoom: 15,
      ...(bounds ? { bounds } : {}),
      attribution: source === "prithvi" ? "Flood extent: Prithvi-EO (Sentinel-2)" : "Flood extent: Sentinel-1 SAR + edge-Otsu",
    });
    map.addLayer(
      { id: MASK_ID, type: "raster", source: MASK_ID, paint: { "raster-opacity": maskOpacity, "raster-resampling": "nearest" } },
      labelLayerId,
    );
  }, [map, loaded, labelLayerId, tiles, showMask, bounds, maskOpacity, source]);

  return null;
}
