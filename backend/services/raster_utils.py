"""COG conversion and mask vectorisation (rasterio only — no shapely dependency)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles


def to_cog(src: Path, dst: Path, nodata: float | None = None) -> Path:
    """Rewrite a GeoTIFF as a Cloud-Optimized GeoTIFF (tiled + overviews + deflate)."""
    profile = cog_profiles.get("deflate")
    profile.update({"BLOCKXSIZE": 512, "BLOCKYSIZE": 512})
    cog_translate(
        str(src),
        str(dst),
        profile,
        nodata=nodata,
        overview_resampling="nearest",
        web_optimized=False,
        quiet=True,
    )
    return dst


def _ring_area_km2(ring: list[list[float]]) -> float:
    """Shoelace area of a lon/lat ring, converted to km² at the ring's mean latitude."""
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx = 111.32 * math.cos(math.radians(lat0))
    ky = 111.32
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        a += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(a) / 2.0


def vectorize_class(
    raster: Path,
    class_value: int,
    target_res_m: float = 60.0,
    min_area_km2: float = 0.02,
) -> tuple[dict[str, Any], float]:
    """Vectorise pixels == class_value into a MultiPolygon GeoJSON (EPSG:4326).

    The raster is read downsampled to ~target_res_m so a district-scale mask
    yields hundreds, not hundreds of thousands, of polygons. Returns
    (geojson_multipolygon, total_area_km2).
    """
    with rasterio.open(raster) as ds:
        px_m = abs(ds.transform.a) * 111_320 * math.cos(math.radians(ds.bounds.top))
        factor = max(1, int(round(target_res_m / px_m)))
        out_shape = (max(1, ds.height // factor), max(1, ds.width // factor))
        data = ds.read(1, out_shape=out_shape, resampling=Resampling.mode)
        transform = ds.transform * ds.transform.scale(ds.width / out_shape[1], ds.height / out_shape[0])

    mask = (data == class_value).astype(np.uint8)
    polys: list[list[list[list[float]]]] = []
    total = 0.0
    for geom, val in shapes(mask, mask=mask.astype(bool), transform=transform):
        if val != 1:
            continue
        area = _ring_area_km2(geom["coordinates"][0])
        if area < min_area_km2:
            continue
        polys.append(geom["coordinates"])
        total += area
    return {"type": "MultiPolygon", "coordinates": polys}, round(total, 3)


def geojson_to_ewkt(geom: dict[str, Any], srid: int = 4326) -> str:
    """MultiPolygon/Polygon GeoJSON → EWKT string PostgREST can cast to geometry."""

    def ring(r: list[list[float]]) -> str:
        return "(" + ", ".join(f"{x} {y}" for x, y in r) + ")"

    def poly(p: list[list[list[float]]]) -> str:
        return "(" + ", ".join(ring(r) for r in p) + ")"

    if geom["type"] == "Polygon":
        body = f"POLYGON{poly(geom['coordinates'])}"
    elif geom["type"] == "MultiPolygon":
        if not geom["coordinates"]:
            body = "MULTIPOLYGON EMPTY"
        else:
            body = "MULTIPOLYGON(" + ", ".join(poly(p) for p in geom["coordinates"]) + ")"
    else:
        raise ValueError(f"unsupported geometry type {geom['type']}")
    return f"SRID={srid};{body}"


def raster_summary(raster: Path) -> dict[str, Any]:
    with rasterio.open(raster) as ds:
        b = ds.bounds
        return {
            "width": ds.width,
            "height": ds.height,
            "crs": str(ds.crs),
            "bounds": [b.left, b.bottom, b.right, b.top],
            "dtype": str(ds.dtypes[0]),
            "nodata": ds.nodata,
        }
