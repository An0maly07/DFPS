"""/api/drought-index (PLAN.md §4.2)."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.regions import REGIONS
from backend.services import supabase_client as db

router = APIRouter(prefix="/api", tags=["drought"])


def _flatten(row: dict[str, Any], district: str) -> dict[str, Any]:
    meta = row.get("meta") or {}
    spi, v, sm, comp = meta.get("spi", {}), meta.get("vhi", {}), meta.get("soil_moisture", {}), meta.get("composite", {})
    return {
        "district": district,
        "recorded_at": row["recorded_at"],
        "target_month": meta.get("target_month"),
        "spi3": row.get("spi"),
        "spei3": row.get("spei"),
        "spi12": spi.get("spi12"),
        "spei12": spi.get("spei12"),
        "spi_through": spi.get("through"),
        "vhi": row.get("vhi"),
        "vci": v.get("vci"),
        "tci": v.get("tci"),
        "vhi_month": f"{v.get('year')}-{v.get('month'):02d}" if v.get("year") else None,
        "soil_moisture": {
            "sm_rootzone": sm.get("sm_rootzone"),
            "sm_z": sm.get("sm_z"),
            "sm_anomaly_pct": sm.get("sm_anomaly_pct"),
            "climatology_years": sm.get("climatology_years"),
        },
        "composite": {
            "category": row.get("composite_category"),
            "score": comp.get("score"),
            "component_categories": comp.get("component_categories"),
            "weights_used": comp.get("weights_used"),
            "scale": ["normal", "mild", "moderate", "severe", "extreme"],
        },
        "series": {
            "dates": spi.get("dates_last24"),
            "spi3": spi.get("spi3_series"),
            "spi12": spi.get("spi12_series"),
            "precip_mm_last12": spi.get("precip_mm_last12"),
        },
        "sources": {
            "spi_spei": "CHIRPS daily + ERA5-Land monthly (Thornthwaite PET), gamma fit 1981-2020, via climate-indices",
            "vhi": "MODIS MOD13A2 NDVI + MOD11A2 LST vs 2001-> monthly climatology",
            "soil_moisture": "SMAP L4 root-zone (SPL4SMGP/008) monthly mean vs 2015-> climatology",
        },
    }


@router.get("/drought-index")
def drought_index(
    district: str = Query("kolhapur-sangli"),
    month: str | None = Query(None, description="YYYY-MM; default = latest stored snapshot"),
    refresh: bool = Query(False, description="Recompute from Earth Engine (~1-2 min) and store"),
) -> dict[str, Any]:
    region = db.get_region(district)
    if region is None:
        raise HTTPException(404, f"unknown district '{district}'")

    target: date | None = None
    if month:
        try:
            y, m = (int(x) for x in month.split("-"))
            target = date(y, m, 1)
        except ValueError:
            raise HTTPException(422, "month must be YYYY-MM")

    rows = db.get_drought_rows(region["id"])
    row = None
    if target:
        row = next((r for r in rows if r["recorded_at"] == target.isoformat()), None)
    elif rows:
        row = rows[0]

    if row is None or refresh:
        from backend.models.drought import compute_drought  # heavy import (ee, climate-indices)

        snap = compute_drought(REGIONS[district], target.replace(day=28) if target else None)
        y, m = (int(x) for x in snap["target_month"].split("-"))
        row = db.upsert_drought_index(
            {
                "region_id": region["id"],
                "recorded_at": date(y, m, 1).isoformat(),
                "spi": snap["spi"].get("spi3"),
                "spei": snap["spi"].get("spei3"),
                "vhi": snap["vhi"].get("vhi"),
                "composite_category": snap["composite"]["category"],
                "meta": snap,
            }
        )
    return _flatten(row, district)


@router.get("/drought-index/history")
def drought_history(district: str = Query("kolhapur-sangli"), limit: int = Query(60, ge=1, le=240)) -> dict[str, Any]:
    region = db.get_region(district)
    if region is None:
        raise HTTPException(404, f"unknown district '{district}'")
    rows = db.get_drought_rows(region["id"], limit)
    return {
        "district": district,
        "rows": [
            {"recorded_at": r["recorded_at"], "spi3": r["spi"], "spei3": r["spei"], "vhi": r["vhi"],
             "composite_category": r["composite_category"]}
            for r in sorted(rows, key=lambda r: r["recorded_at"])
        ],
    }
