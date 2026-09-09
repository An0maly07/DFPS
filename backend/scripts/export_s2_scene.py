"""Export a Sentinel-2 L2A scene (Prithvi band set + SCL) over a region as one GeoTIFF.

Prithvi-EO-1.0 Sen1Floods11 expects six S2 bands in this order:
    Blue (B2), Green (B3), Red (B4), Narrow NIR (B8A), SWIR-1 (B11), SWIR-2 (B12)
We append the Scene Classification Layer (SCL) as band 7 for cloud masking.

Earth Engine's getDownloadURL is capped at ~50 MB per request, so the AOI is split
into a grid of tiles that are downloaded separately and merged locally.

Usage:
  python -m backend.scripts.export_s2_scene 2021-07-28 --region kolhapur-sangli \
      --bounds 74.10 16.55 74.75 16.95 --scale 10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ee
import rasterio
from rasterio.merge import merge

from backend.config import DATA_DIR
from backend.regions import REGIONS
from backend.services.gee_client import download_geotiff, init_ee, region_rectangle

S2_BANDS = ["B2", "B3", "B4", "B8A", "B11", "B12"]
PRITHVI_DIR = DATA_DIR / "prithvi"


def s2_mosaic(date: str, bounds: list[float]) -> ee.Image:
    aoi = region_rectangle(bounds)
    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(date, ee.Date(date).advance(1, "day"))
    )
    n = col.size().getInfo()
    if n == 0:
        raise SystemExit(f"no Sentinel-2 scene over {bounds} on {date}")
    print(f"{n} S2 granule(s) on {date}")
    # bands are stored as int16 reflectance*10000; SCL is uint8 → cast the stack to int16
    return col.mosaic().select(S2_BANDS + ["SCL"]).toInt16()


def tile_bounds(b: list[float], nx: int, ny: int):
    x0, y0, x1, y1 = b
    dx, dy = (x1 - x0) / nx, (y1 - y0) / ny
    for j in range(ny):
        for i in range(nx):
            yield i, j, [x0 + i * dx, y0 + j * dy, x0 + (i + 1) * dx, y0 + (j + 1) * dy]


def export_scene(date: str, region: str, bounds: list[float], scale: float, nx: int, ny: int) -> Path:
    init_ee()
    img = s2_mosaic(date, bounds)
    raw_dir = PRITHVI_DIR / "_raw" / f"{region}_{date}"
    tiles = []
    for i, j, tb in tile_bounds(bounds, nx, ny):
        path = raw_dir / f"tile_{i}_{j}.tif"
        if not path.exists():
            print(f"downloading tile {i},{j} {['%.3f' % v for v in tb]} …", flush=True)
            download_geotiff(img, region_rectangle(tb), scale, path)
        tiles.append(path)

    out = PRITHVI_DIR / f"s2_{region}_{date}.tif"
    srcs = [rasterio.open(p) for p in tiles]
    mosaic, transform = merge(srcs, method="first")
    profile = srcs[0].profile.copy()
    for s in srcs:
        s.close()
    profile.update(
        height=mosaic.shape[1], width=mosaic.shape[2], transform=transform,
        compress="deflate", tiled=True, blockxsize=512, blockysize=512, nodata=0,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(mosaic)
        dst.descriptions = tuple(S2_BANDS + ["SCL"])
    print(f"wrote {out}  shape={mosaic.shape}  ({out.stat().st_size / 1e6:.1f} MB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--region", default="kolhapur-sangli")
    ap.add_argument("--bounds", nargs=4, type=float, help="min_lon min_lat max_lon max_lat (default: region bounds)")
    ap.add_argument("--scale", type=float, default=10)
    ap.add_argument("--nx", type=int, default=4)
    ap.add_argument("--ny", type=int, default=3)
    a = ap.parse_args()
    bounds = a.bounds or REGIONS[a.region].bounds
    export_scene(a.date, a.region, bounds, a.scale, a.nx, a.ny)
    return 0


if __name__ == "__main__":
    sys.exit(main())
