"""Compute the drought snapshot for a region/month and (optionally) upsert drought_index.

Usage:
  python -m backend.scripts.compute_drought_index --region kolhapur-sangli
  python -m backend.scripts.compute_drought_index --region marathwada-beed --month 2018-10 --write
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from backend.models.drought import compute_drought
from backend.regions import get_region
from backend.services import supabase_client as db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="kolhapur-sangli")
    ap.add_argument("--month", help="YYYY-MM (default: last complete month)")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    region = get_region(args.region)
    target = None
    if args.month:
        y, m = (int(x) for x in args.month.split("-"))
        target = date(y, m, 28)
    snap = compute_drought(region, target)
    printable = {k: v for k, v in snap.items() if k != "spi"}
    printable["spi"] = {k: v for k, v in snap["spi"].items() if not k.endswith("_series") and k != "dates_last24"}
    print(json.dumps(printable, indent=1))

    if args.write:
        row = db.get_region(region.name)
        if row is None:
            print(f"region {region.name} not in DB — apply schema first")
            return 2
        y, m = (int(x) for x in snap["target_month"].split("-"))
        saved = db.upsert_drought_index(
            {
                "region_id": row["id"],
                "recorded_at": date(y, m, 1).isoformat(),
                "spi": snap["spi"].get("spi3"),
                "spei": snap["spi"].get("spei3"),
                "vhi": snap["vhi"].get("vhi"),
                "composite_category": snap["composite"]["category"],
                "meta": snap,
            }
        )
        print("drought_index row:", saved["id"], saved["recorded_at"], saved["composite_category"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
