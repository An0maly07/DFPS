"""Run the SAR + Otsu pipeline for one or more event dates, export COGs, upload them to
Supabase Storage and register rows in flood_extents.

Usage:
  python -m backend.scripts.export_flood_extent 2021-07-23
  python -m backend.scripts.export_flood_extent 2021-07-11 2021-07-23 2021-08-04 --no-upload
  python -m backend.scripts.export_flood_extent --list 2021-07-23     # just list S1 scenes
  python -m backend.scripts.export_flood_extent --reregister 2021-07-22   # rebuild DB row
                                                  # from the local COG sidecar (no GEE)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from backend.config import COG_DIR
from backend.models.sar_otsu import (
    AOI_KOLHAPUR_SANGLI,
    FloodExtentResult,
    compute_flood_extent,
    list_s1_scenes,
)
from backend.services import supabase_client as db
from backend.services.raster_utils import geojson_to_ewkt, vectorize_class


def load_result(region: str, capture_day: str) -> FloodExtentResult:
    """Rebuild a FloodExtentResult from its sidecar, re-vectorising from the mask COG."""
    sidecar = COG_DIR / f"flood_mask_{region}_{capture_day}.json"
    data = json.loads(sidecar.read_text())
    data["geom"], vec_km2 = vectorize_class(Path(data["mask_cog"]), 3)
    print(f"  re-vectorised {sidecar.name}: {len(data['geom']['coordinates'])} polygons, {vec_km2} km²")
    result = FloodExtentResult(**data)
    result.save()
    return result


def register(result, upload: bool) -> None:
    region = db.get_region(result.region)
    if region is None:
        print(f"  region '{result.region}' not in Supabase yet — run apply_schema first; skipping DB write")
        return
    # Public URLs are deterministic, so --no-upload still records the right links.
    urls = {}
    for key in ("mask_cog", "vv_post_cog", "vv_pre_cog"):
        p = Path(getattr(result, key))
        object_name = f"{result.region}/{p.name}"
        if upload:
            urls[key] = db.upload_cog(p, object_name)
            print(f"  uploaded {p.name} -> {urls[key]}")
        else:
            urls[key] = db.cog_public_url(object_name)
    meta = {k: v for k, v in asdict(result).items() if k != "geom"}
    meta["urls"] = urls
    row = db.upsert_flood_extent(
        region_id=region["id"],
        source="sar_otsu",
        captured_at=result.captured_at,
        cog_url=urls["mask_cog"],
        geom_ewkt=geojson_to_ewkt(result.geom) if result.geom.get("coordinates") else None,
        meta=meta,
    )
    print(f"  flood_extents row id={row['id']} captured_at={row['captured_at']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="+", help="event dates YYYY-MM-DD")
    ap.add_argument("--region", default="kolhapur-sangli")
    ap.add_argument("--list", action="store_true", help="only list available S1 scenes")
    ap.add_argument("--no-upload", action="store_true", help="skip Supabase Storage upload")
    ap.add_argument(
        "--reregister",
        action="store_true",
        help="dates are capture days; rebuild geometry from local COGs and upsert DB rows",
    )
    args = ap.parse_args()

    for d in args.dates:
        if args.list:
            print(d, json.dumps(list_s1_scenes(d, AOI_KOLHAPUR_SANGLI), indent=1))
            continue
        if args.reregister:
            print(f"== reregister {args.region} @ {d}")
            register(load_result(args.region, d), upload=not args.no_upload)
            continue
        print(f"== {args.region} @ {d}")
        r = compute_flood_extent(d, region_name=args.region)
        print(
            f"  scene {r.captured_at} ({r.orbit_pass}, track {r.relative_orbit}) "
            f"otsu post={r.otsu_threshold_post_db} dB pre={r.otsu_threshold_pre_db} dB "
            f"flood={r.flood_area_km2} km²"
        )
        print(f"  cog: {r.mask_cog}")
        register(r, upload=not args.no_upload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
