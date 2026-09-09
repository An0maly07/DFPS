"""Run Prithvi (ONNX) over an exported S2 scene, write class + true-colour COGs, upload, and
register a `source='prithvi'` row in flood_extents so /api/flood-extent?source=prithvi works.

  python -m backend.scripts.export_prithvi_extent 2021-07-28 --sar-ref 2021-08-03 [--stride 112] [--no-upload]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio

from backend.config import COG_DIR
from backend.models import prithvi_infer as pi
from backend.models.prithvi_data import PRITHVI_DIR
from backend.services import supabase_client as db
from backend.services.raster_utils import geojson_to_ewkt, raster_summary, to_cog, vectorize_class


def true_colour_cog(scene: Path, out: Path) -> Path:
    """B4,B3,B2 → 8-bit RGB COG (0–0.30 reflectance stretch, gamma 0.8) for the optical basemap.

    JPEG-in-COG with an internal mask: ~10× smaller than deflate, which matters because
    Supabase Storage rejects objects over 50 MB on the free tier (HTTP 413).
    """
    from rio_cogeo.cogeo import cog_translate
    from rio_cogeo.profiles import cog_profiles

    with rasterio.open(scene) as s2:
        rgb = s2.read([3, 2, 1]).astype(np.float32) * 1e-4
        prof = s2.profile.copy()
        nodata = (rgb == 0).all(axis=0)
    rgb = np.clip(rgb / 0.30, 0, 1) ** 0.8 * 254 + 1
    rgb = rgb.astype(np.uint8)
    rgb[:, nodata] = 0
    raw = out.with_name(out.stem + "_raw.tif")
    prof.update(count=3, dtype="uint8", nodata=0, compress="deflate", photometric="RGB")
    with rasterio.open(raw, "w", **prof) as dst:
        dst.write(rgb)
    profile = cog_profiles.get("jpeg")
    profile.update({"BLOCKXSIZE": 512, "BLOCKYSIZE": 512, "QUALITY": 85})
    cog_translate(str(raw), str(out), profile, nodata=0, add_mask=True, overview_resampling="average",
                  web_optimized=False, quiet=True)
    raw.unlink(missing_ok=True)
    return out


def stats_from_class_raster(path: Path, threshold: float) -> dict:
    """Recompute the summary stats from an existing class raster (lets --skip-inference reuse it)."""
    with rasterio.open(path) as ds:
        cls = ds.read(1)
        px_km2 = abs(ds.transform.a * 111.32 * np.cos(np.radians(ds.bounds.top))) * abs(ds.transform.e * 111.32)
    return {
        "flood_area_km2": round(float((cls == pi.CLASS_FLOOD).sum() * px_km2), 2),
        "permanent_water_km2": round(float((cls == pi.CLASS_PERM).sum() * px_km2), 2),
        "cloud_pct": round(float((cls == pi.CLASS_CLOUD).mean() * 100), 1),
        "usable_pct": round(float(np.isin(cls, [pi.CLASS_LAND, pi.CLASS_PERM, pi.CLASS_FLOOD]).mean() * 100), 1),
        "pixel_km2": px_km2,
        "water_threshold": threshold,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", help="S2 scene date (YYYY-MM-DD) already exported by export_s2_scene")
    ap.add_argument("--region", default="kolhapur-sangli")
    ap.add_argument("--sar-ref", default="2021-08-03", help="SAR mask date used for permanent-water class")
    ap.add_argument("--stride", type=int, default=112)
    ap.add_argument("--backend", choices=["onnx", "torch"], default="onnx", help="torch = GPU weights (fast offline)")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--skip-inference", action="store_true", help="reuse the existing raw class raster")
    a = ap.parse_args()

    scene = PRITHVI_DIR / f"s2_{a.region}_{a.date}.tif"
    sar_ref = COG_DIR / f"flood_mask_{a.region}_{a.sar_ref}.tif"
    tag = f"{a.region}_{a.date}"
    raw_cls = PRITHVI_DIR / "_raw" / f"prithvi_mask_{tag}.tif"

    def progress(done, total):
        print(f"  windows {done}/{total}", flush=True)

    fmeta = pi.meta(a.region)
    if a.skip_inference and raw_cls.exists():
        print(f"== reusing {raw_cls.name}", flush=True)
        stats = stats_from_class_raster(raw_cls, float(fmeta.get("water_threshold", 0.5)))
    else:
        print(f"== Prithvi inference {tag} (stride {a.stride})", flush=True)
        stats = pi.classify_scene(scene, sar_ref if sar_ref.exists() else None, raw_cls, stride=a.stride,
                                  region=a.region, progress=progress, backend=a.backend)
    mask_cog = to_cog(raw_cls, COG_DIR / f"prithvi_mask_{tag}.tif", nodata=0)
    rgb_cog = true_colour_cog(scene, COG_DIR / f"s2_rgb_{tag}.tif")
    geom, vec_km2 = vectorize_class(mask_cog, pi.CLASS_FLOOD)
    print(f"  flood {stats['flood_area_km2']} km² (vectorised {vec_km2}), cloud {stats['cloud_pct']} %, "
          f"usable {stats['usable_pct']} %", flush=True)

    region = db.get_region(a.region)
    if region is None:
        print("region not in DB"); return 2
    urls = {}
    for key, p in (("mask_cog", mask_cog), ("s2_rgb_cog", rgb_cog)):
        obj = f"{a.region}/{p.name}"
        urls[key] = db.upload_cog(p, obj) if not a.no_upload else db.cog_public_url(obj)
        if not a.no_upload:
            print(f"  uploaded {p.name}")
    meta = {
        "region": a.region, "event_date": a.date, "captured_at": a.date, "scene": scene.name,
        "sensor": "Sentinel-2 L2A (optical)", "model_version": fmeta.get("model_version", "prithvi"),
        "sar_reference": a.sar_ref, "flood_area_km2": stats["flood_area_km2"],
        "permanent_water_km2": stats["permanent_water_km2"], "cloud_pct": stats["cloud_pct"],
        "usable_pct": stats["usable_pct"], "bounds": raster_summary(mask_cog)["bounds"],
        "mask_cog": str(mask_cog), "s2_rgb_cog": str(rgb_cog), "urls": urls,
        "val_metrics": {"zero_shot": fmeta.get("zero_shot_val"), "fine_tuned": fmeta.get("best_val")},
        "label_protocol": fmeta.get("label_protocol"),
        "classes": {"0": "no data", "1": "land", "2": "permanent/pre-event water", "3": "flood", "4": "cloud/shadow"},
    }
    (COG_DIR / f"prithvi_mask_{tag}.json").write_text(json.dumps({**meta, "geom": geom}, indent=1))
    row = db.upsert_flood_extent(
        region_id=region["id"], source="prithvi",
        captured_at=datetime(*[int(x) for x in a.date.split("-")], tzinfo=timezone.utc).isoformat(),
        cog_url=urls["mask_cog"], geom_ewkt=geojson_to_ewkt(geom) if geom["coordinates"] else None, meta=meta,
    )
    print(f"  flood_extents row id={row['id']} source=prithvi captured_at={row['captured_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
