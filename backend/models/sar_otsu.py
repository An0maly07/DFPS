"""Sentinel-1 SAR + Otsu flood-extent pipeline (PLAN.md §3.1).

Server-side (Earth Engine): scene selection, speckle filtering, masking, class map.
Client-side (Python): Otsu threshold from the GEE histogram via scikit-image, GeoTIFF
download, COG conversion, vectorisation.

Output class map (uint8):
    0 = no SAR coverage      1 = land
    2 = permanent / pre-event water      3 = flood (new water)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import ee
import numpy as np
from skimage.filters import threshold_otsu

from backend.config import COG_DIR
from backend.services.gee_client import download_geotiff, init_ee, region_rectangle
from backend.services.raster_utils import raster_summary, to_cog, vectorize_class

# Kolhapur city, Ichalkaranji, Sangli–Miraj and the Panchganga/Krishna floodplain.
AOI_KOLHAPUR_SANGLI = [73.90, 16.35, 74.75, 17.05]

S1_COLLECTION = "COPERNICUS/S1_GRD"
GSW_SEASONALITY_MONTHS = 10  # JRC GSW pixels wet >= this many months/yr = permanent water
MAX_SLOPE_DEG = 5           # SAR "water" on steep slopes is radar shadow, not flood
MIN_CLUSTER_PX = 8          # drop isolated speckle blobs smaller than this
SPECKLE_RADIUS_M = 50

# Edge-Otsu (Markert et al. 2020): a whole-AOI histogram is multimodal (forest ~-7 dB,
# wet plains ~-12 dB, water ~-20 dB) and plain Otsu splits forest/plains. Sampling
# the histogram only near water/land edges makes it bimodal water/land.
EDGE_INITIAL_DB = -16.0     # seed water guess used only to locate edges
EDGE_MIN_LENGTH_PX = 20     # drop short/noisy edge fragments
EDGE_BUFFER_M = 100
# Plausible VV water/land threshold band; outside it the edge histogram was degenerate
# (e.g. no water in scene) and a fixed fallback is used and recorded in the sidecar.
THRESHOLD_BAND_DB = (-22.0, -10.0)
FALLBACK_THRESHOLD_DB = -15.0


@dataclass
class FloodExtentResult:
    region: str
    event_date: str
    captured_at: str
    pre_window: list[str]
    scene_ids: list[str]
    orbit_pass: str
    relative_orbit: int
    otsu_threshold_post_db: float
    otsu_threshold_pre_db: float
    threshold_method_post: str
    threshold_method_pre: str
    flood_area_km2: float
    mask_cog: str
    vv_post_cog: str
    vv_pre_cog: str
    scale_m: float
    bounds: list[float]
    geom: dict[str, Any] = field(default_factory=dict)

    def sidecar_path(self) -> Path:
        return Path(self.mask_cog).with_suffix(".json")

    def save(self) -> Path:
        p = self.sidecar_path()
        p.write_text(json.dumps(asdict(self), indent=2))
        return p


def _s1_vv(start: str, end: str, aoi: ee.Geometry) -> ee.ImageCollection:
    return (
        ee.ImageCollection(S1_COLLECTION)
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.eq("resolution_meters", 10))
        .select("VV")
    )


def _edge_otsu(image: ee.Image, aoi: ee.Geometry, scale_m: float) -> tuple[float, str, dict]:
    """Otsu threshold sampled only near water/land edges (edge-Otsu).

    Returns (threshold_dB, method, diagnostics). method is 'edge_otsu' or
    'fallback_fixed' when the edge histogram is empty/degenerate or the threshold
    falls outside THRESHOLD_BAND_DB.
    """
    seed_water = image.lt(EDGE_INITIAL_DB)
    canny = ee.Algorithms.CannyEdgeDetector(image=seed_water, threshold=0.1, sigma=1)
    edges = canny.gt(0).selfMask()
    long_edges = edges.connectedPixelCount(EDGE_MIN_LENGTH_PX * 2, True).gte(EDGE_MIN_LENGTH_PX)
    near_edges = long_edges.unmask(0).focalMax(EDGE_BUFFER_M, "circle", "meters").gt(0)

    hist = (
        image.updateMask(near_edges)
        .updateMask(image.gt(-35))
        .reduceRegion(
            reducer=ee.Reducer.histogram(maxBuckets=256, minBucketWidth=0.1),
            geometry=aoi,
            scale=scale_m,
            maxPixels=1e10,
            bestEffort=True,
        )
        .get("VV")
        .getInfo()
    )
    diag: dict = {"edge_pixels": 0, "raw_otsu_db": None}
    if not hist or not hist.get("histogram"):
        return FALLBACK_THRESHOLD_DB, "fallback_fixed", diag
    counts = np.asarray(hist["histogram"], dtype=float)
    centers = np.asarray(hist["bucketMeans"], dtype=float)
    diag["edge_pixels"] = int(counts.sum())
    if counts.sum() < 1000:
        return FALLBACK_THRESHOLD_DB, "fallback_fixed", diag
    t = float(threshold_otsu(hist=(counts, centers)))
    diag["raw_otsu_db"] = round(t, 3)
    if not (THRESHOLD_BAND_DB[0] <= t <= THRESHOLD_BAND_DB[1]):
        return FALLBACK_THRESHOLD_DB, "fallback_fixed", diag
    return t, "edge_otsu", diag


def list_s1_scenes(event_date: str, aoi_bounds: list[float], window_days: tuple[int, int] = (2, 5)) -> list[dict]:
    """Scenes available around an event date — handy for picking dates for the time-slider."""
    init_ee()
    aoi = region_rectangle(aoi_bounds)
    ev = datetime.fromisoformat(event_date).date()
    start = (ev - timedelta(days=window_days[0])).isoformat()
    end = (ev + timedelta(days=window_days[1] + 1)).isoformat()
    col = _s1_vv(start, end, aoi)
    feats = col.getInfo()["features"]
    out = []
    for f in feats:
        p = f["properties"]
        out.append(
            {
                "id": f["id"],
                "date": datetime.utcfromtimestamp(p["system:time_start"] / 1000).date().isoformat(),
                "pass": p["orbitProperties_pass"],
                "relative_orbit": p["relativeOrbitNumber_start"],
                "platform": p["platform_number"],
            }
        )
    return sorted(out, key=lambda s: s["date"])


def compute_flood_extent(
    event_date: str,
    region_name: str = "kolhapur-sangli",
    aoi_bounds: list[float] = AOI_KOLHAPUR_SANGLI,
    post_window_days: tuple[int, int] = (2, 5),
    pre_window_days: tuple[int, int] = (70, 15),
    mask_scale_m: float = 20,
    vv_scale_m: float = 30,
    out_dir: Path = COG_DIR,
    dry_run: bool = False,
) -> FloodExtentResult | dict:
    """Run the full SAR + Otsu pipeline for one event date and export COGs.

    post_window_days = (days before, days after) event_date to search for the
    flood-time scene; the acquisition closest to event_date is used.
    pre_window_days  = (days before start, days before end) for the pre-flood
    median reference, restricted to the same relative orbit as the flood scene.
    dry_run=True stops after threshold estimation and returns the diagnostics.
    """
    init_ee()
    aoi = region_rectangle(aoi_bounds)
    ev = datetime.fromisoformat(event_date).date()

    # --- flood-time scene: closest acquisition to the event date ------------------
    scenes = list_s1_scenes(event_date, aoi_bounds, post_window_days)
    if not scenes:
        raise RuntimeError(f"No Sentinel-1 IW VV scenes over {region_name} within {post_window_days} days of {event_date}")
    closest = min(scenes, key=lambda s: abs((date.fromisoformat(s["date"]) - ev).days))
    capture_day = closest["date"]
    orbit_pass, rel_orbit = closest["pass"], closest["relative_orbit"]
    same_track = [s for s in scenes if s["date"] == capture_day and s["relative_orbit"] == rel_orbit]

    post_col = (
        _s1_vv(capture_day, (date.fromisoformat(capture_day) + timedelta(days=1)).isoformat(), aoi)
        .filter(ee.Filter.eq("relativeOrbitNumber_start", rel_orbit))
    )
    post_raw = post_col.mosaic().clip(aoi)

    # --- pre-flood reference: median of the same track over the pre-window ---------
    pre_start = (ev - timedelta(days=pre_window_days[0])).isoformat()
    pre_end = (ev - timedelta(days=pre_window_days[1])).isoformat()
    pre_col = _s1_vv(pre_start, pre_end, aoi).filter(ee.Filter.eq("relativeOrbitNumber_start", rel_orbit))
    if pre_col.size().getInfo() == 0:  # fall back to same pass direction, any track
        pre_col = _s1_vv(pre_start, pre_end, aoi).filter(ee.Filter.eq("orbitProperties_pass", orbit_pass))
    pre_raw = pre_col.median().clip(aoi)

    # --- speckle filter (focal mean, 50 m) ------------------------------------------
    post = post_raw.focalMean(SPECKLE_RADIUS_M, "circle", "meters").rename("VV")
    pre = pre_raw.focalMean(SPECKLE_RADIUS_M, "circle", "meters").rename("VV")

    # --- edge-Otsu thresholds (histogram computed in GEE, threshold in scikit-image) --
    t_post, m_post, d_post = _edge_otsu(post, aoi, vv_scale_m)
    t_pre, m_pre, d_pre = _edge_otsu(pre, aoi, vv_scale_m)
    if dry_run:
        return {
            "captured_at": capture_day,
            "pre_window": [pre_start, pre_end],
            "post": {"threshold_db": t_post, "method": m_post, **d_post},
            "pre": {"threshold_db": t_pre, "method": m_pre, **d_pre},
        }

    water_post = post.lt(t_post)
    water_pre = pre.lt(t_pre)

    # --- ancillary masks --------------------------------------------------------------
    permanent = (
        ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("seasonality").gte(GSW_SEASONALITY_MONTHS).unmask(0)
    )
    steep = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003")).gt(MAX_SLOPE_DEG)

    flood = water_post.And(water_pre.Not()).And(permanent.Not()).And(steep.Not())
    cluster = flood.selfMask().connectedPixelCount(MIN_CLUSTER_PX * 3, True)
    flood = flood.updateMask(cluster.gte(MIN_CLUSTER_PX)).unmask(0)

    classes = (
        ee.Image.constant(1)
        .where(permanent.Or(water_pre), 2)
        .where(flood, 3)
        .updateMask(post_raw.mask())
        .unmask(0)
        .toUint8()
        .rename("class")
        .clip(aoi)
    )

    area_m2 = (
        flood.multiply(ee.Image.pixelArea())
        .reduceRegion(ee.Reducer.sum(), aoi, scale=mask_scale_m, maxPixels=1e10, bestEffort=True)
        .get("VV")
        .getInfo()
    )

    # --- export -----------------------------------------------------------------------
    tag = f"{region_name}_{capture_day}"
    raw_dir = out_dir / "_raw"
    mask_raw = download_geotiff(classes, aoi, mask_scale_m, raw_dir / f"flood_mask_{tag}.tif")
    vv_post_raw = download_geotiff(post.toFloat(), aoi, vv_scale_m, raw_dir / f"s1_vv_post_{tag}.tif")
    vv_pre_raw = download_geotiff(pre.toFloat(), aoi, vv_scale_m, raw_dir / f"s1_vv_pre_{tag}.tif")

    mask_cog = to_cog(mask_raw, out_dir / f"flood_mask_{tag}.tif", nodata=0)
    vv_post_cog = to_cog(vv_post_raw, out_dir / f"s1_vv_post_{tag}.tif")
    vv_pre_cog = to_cog(vv_pre_raw, out_dir / f"s1_vv_pre_{tag}.tif")

    geom, _ = vectorize_class(mask_cog, 3)
    bounds = raster_summary(mask_cog)["bounds"]

    result = FloodExtentResult(
        region=region_name,
        event_date=event_date,
        captured_at=capture_day,
        pre_window=[pre_start, pre_end],
        scene_ids=[s["id"] for s in same_track],
        orbit_pass=orbit_pass,
        relative_orbit=rel_orbit,
        otsu_threshold_post_db=round(t_post, 3),
        otsu_threshold_pre_db=round(t_pre, 3),
        threshold_method_post=m_post,
        threshold_method_pre=m_pre,
        flood_area_km2=round((area_m2 or 0) / 1e6, 2),
        mask_cog=str(mask_cog),
        vv_post_cog=str(vv_post_cog),
        vv_pre_cog=str(vv_pre_cog),
        scale_m=mask_scale_m,
        bounds=bounds,
        geom=geom,
    )
    result.save()
    return result
