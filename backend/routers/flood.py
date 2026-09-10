"""/api/forecast and /api/flood-extent (PLAN.md §4.2)."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query

from backend.config import COG_DIR, DATA_DIR, settings
from backend.models.features import LEAD_DAYS, REGION_POINTS
from backend.regions import REGIONS
from backend.services import open_meteo as om
from backend.services import supabase_client as db

router = APIRouter(prefix="/api", tags=["flood"])

# Class raster colours (0 = nodata handled by nodata=0 -> transparent)
MASK_COLORMAP = {"1": [0, 0, 0, 0], "2": [40, 90, 200, 230], "3": [220, 30, 30, 230], "4": [160, 160, 160, 200]}
AREA_NOTE = {
    "sar_otsu": "SAR-detected open water only; flooding under vegetation/buildings is not visible",
    "prithvi": "Optical (Sentinel-2) water from the Prithvi model; pixels under cloud/shadow are class 4 and cannot be assessed",
}
MAX_DATE_GAP_DAYS = 16  # one S1 revisit + slack


def _thresholds(region: str) -> dict[str, float] | None:
    p = DATA_DIR / "training" / f"{region}_thresholds.json"
    return json.loads(p.read_text()) if p.exists() else None


def _nearest_region(lat: float, lon: float) -> str:
    return min(REGIONS.values(), key=lambda r: (r.lat - lat) ** 2 + (r.lon - lon) ** 2).name


def cog_ref(cog_url: str) -> str:
    """What TiTiler should open: the local COG when we have it (dev/demo), else the URL."""
    if settings.cog_source == "auto":
        local = COG_DIR / cog_url.rsplit("/", 1)[-1]
        if local.exists():
            return local.as_posix()
    return cog_url


def tile_url(cog_url: str, **params: Any) -> str:
    q = urlencode({"url": cog_ref(cog_url), **params})
    return f"{settings.titiler_url}/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png?{q}"


def tile_templates(urls: dict[str, str]) -> dict[str, str]:
    out = {}
    if urls.get("mask_cog"):
        out["mask"] = tile_url(urls["mask_cog"], nodata=0, colormap=json.dumps(MASK_COLORMAP), resampling="nearest")
    for key, name in (("vv_post_cog", "vv_post"), ("vv_pre_cog", "vv_pre")):
        if urls.get(key):
            out[name] = tile_url(urls[key], rescale="-25,0", colormap_name="gray")
    if urls.get("s2_rgb_cog"):
        out["s2_rgb"] = tile_url(urls["s2_rgb_cog"], nodata=0)
    return out


@router.get("/forecast")
def forecast(
    lat: float = Query(settings.default_lat),
    lon: float = Query(settings.default_lon),
    days: int = Query(7, ge=1, le=16),
    past_days: int = Query(7, ge=0, le=30),
) -> dict[str, Any]:
    """Rainfall/temperature/soil-moisture forecast + GloFAS discharge (Open-Meteo passthrough)."""
    try:
        weather = om.get_weather_forecast(lat, lon, forecast_days=days, past_days=past_days)
        flood = om.get_flood_forecast(lat, lon, forecast_days=days, past_days=past_days)
    except om.OpenMeteoError as e:
        raise HTTPException(502, f"Open-Meteo unavailable: {e}")

    region = _nearest_region(lat, lon)
    today = date.today().isoformat()
    future = [q for d, q in zip(flood["dates"], flood["river_discharge"]) if d > today and q is not None]
    return {
        "lat": lat,
        "lon": lon,
        "region": region,
        "today": today,
        "weather": {
            "dates": weather["dates"],
            "precipitation_mm": weather["precipitation_sum"],
            "temperature_mean_c": weather["temperature_2m_mean"],
            "temperature_max_c": weather["temperature_2m_max"],
            "soil_moisture_pct": weather["soil_moisture_pct"],
            "et0_mm": weather["et0_fao_evapotranspiration"],
        },
        "discharge": {
            "dates": flood["dates"],
            "river_discharge": flood["river_discharge"],
            "ensemble_mean": flood["river_discharge_mean"],
            "ensemble_max": flood["river_discharge_max"],
            "ensemble_min": flood["river_discharge_min"],
            "p25": flood["river_discharge_p25"],
            "p75": flood["river_discharge_p75"],
            "units": "m3/s",
            "source": "Open-Meteo Flood API (GloFAS v4)",
            f"max_next_{LEAD_DAYS}d": max(future[:LEAD_DAYS]) if future else None,
        },
        # return-period flows derived from the GloFAS reanalysis -> chart threshold lines
        "thresholds": _thresholds(region),
    }


@router.get("/flood-extent")
def flood_extent(
    district: str = Query("kolhapur-sangli"),
    date_: str | None = Query(None, alias="date", description="YYYY-MM-DD (nearest snapshot within 16 days) or 'peak'"),
    source: str = Query("sar_otsu", pattern="^(sar_otsu|prithvi)$"),
    include_geojson: bool = Query(False),
) -> dict[str, Any]:
    """COG URLs, TiTiler tile templates and (optionally) GeoJSON for a flood-extent snapshot."""
    region = db.get_region(district)
    if region is None:
        raise HTTPException(404, f"unknown district '{district}'")
    rows = db.get_flood_extents(region["id"], source)
    if not rows:
        raise HTTPException(404, f"no {source} flood extents for {district} yet")

    available = [r["captured_at"][:10] for r in rows]
    snapshots = [
        {"date": r["captured_at"][:10], "flood_area_km2": (r["meta"] or {}).get("flood_area_km2"), "event_date": (r["meta"] or {}).get("event_date")}
        for r in rows
    ]
    if date_ == "peak":
        date_ = max(snapshots, key=lambda s: s["flood_area_km2"] or 0)["date"]
    if date_:
        try:
            target = date.fromisoformat(date_)
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD or 'peak'")
        row = min(rows, key=lambda r: abs((datetime.fromisoformat(r["captured_at"]).date() - target).days))
        gap = abs((datetime.fromisoformat(row["captured_at"]).date() - target).days)
        if gap > MAX_DATE_GAP_DAYS:
            raise HTTPException(404, {"error": f"no snapshot within {MAX_DATE_GAP_DAYS} days of {date_}", "available_dates": available})
    else:
        row = rows[-1]

    meta = row["meta"] or {}
    urls = meta.get("urls", {})
    out: dict[str, Any] = {
        "district": district,
        "source": source,
        "captured_at": row["captured_at"][:10],
        "event_date": meta.get("event_date"),
        "scene_ids": meta.get("scene_ids"),
        "cog_url": row["cog_url"],
        "urls": urls,
        "tiles": tile_templates(urls),
        "bounds": meta.get("bounds"),
        "flood_area_km2": meta.get("flood_area_km2"),
        "area_note": AREA_NOTE[source],
        "thresholds_db": {
            "post": meta.get("otsu_threshold_post_db"),
            "pre": meta.get("otsu_threshold_pre_db"),
            "method": meta.get("threshold_method_post"),
        },
        "classes": meta.get("classes") or {"0": "no data", "1": "land", "2": "permanent/pre-event water", "3": "flood"},
        "model": {
            "version": meta.get("model_version"), "sensor": meta.get("sensor"),
            "cloud_pct": meta.get("cloud_pct"), "usable_pct": meta.get("usable_pct"),
            "val_metrics": meta.get("val_metrics"),
        } if source == "prithvi" else None,
        "available_dates": available,
        "snapshots": snapshots,
        "peak_date": max(snapshots, key=lambda s: s["flood_area_km2"] or 0)["date"],
    }
    if include_geojson:
        out["geojson"] = db.flood_extent_geojson(row["id"])
    return out


@router.get("/regions")
def regions() -> dict[str, Any]:
    fc = db.regions_geojson()
    for f in fc.get("features", []):
        name = f["properties"]["name"]
        # flood_model: the risk model is trained per basin, so it does not cover every region
        # (see /api/risk-score, which 422s otherwise). Lets the UI say so before it asks.
        f["properties"]["flood_model"] = name in REGION_POINTS
        r = REGIONS.get(name)
        if r:
            f["properties"].update({"lat": r.lat, "lon": r.lon, "bounds": r.bounds})
    return fc
