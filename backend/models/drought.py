"""Drought index pipeline (PLAN.md §3.5): SPI/SPEI (CHIRPS + ERA5-Land), VHI (MODIS),
SMAP root-zone soil-moisture anomaly, combined into a Combined Drought Indicator.

All regional means are computed server-side in Earth Engine; the index maths runs
locally (climate-indices for SPI/SPEI, numpy for the rest).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import ee
import numpy as np
from climate_indices import compute, indices

from backend.regions import Region
from backend.services.gee_client import init_ee, region_rectangle

# climate-indices logs every fit at INFO through structlog; keep the API logs clean.
try:
    import structlog

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
except Exception:  # pragma: no cover - logging is best-effort
    pass
# climate-indices also installs a stdlib root handler; without this every httpx call
# from the Supabase client is echoed at INFO.
logging.getLogger().setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

CHIRPS = "UCSB-CHG/CHIRPS/DAILY"
ERA5L_MONTHLY = "ECMWF/ERA5_LAND/MONTHLY_AGGR"
MOD13 = "MODIS/061/MOD13A2"   # NDVI, 16-day, 1 km (scale 0.0001)
MOD11 = "MODIS/061/MOD11A2"   # LST day, 8-day, 1 km (K, scale 0.02)
SMAP = "NASA/SMAP/SPL4SMGP/008"  # 3-hourly, ~10 km, 2015-03-31 -> (007 is deprecated)

CLIMATOLOGY_START = 1981
CALIBRATION = (1981, 2020)
MODIS_START = 2001
SMAP_START = 2015

CATEGORY_NAMES = ["normal", "mild", "moderate", "severe", "extreme"]


# --- Earth Engine regional series ----------------------------------------------------------

def _monthly_series(collection: str, band: str, aoi: ee.Geometry, start_year: int, end: date,
                    reducer_per_month: str, scale_m: float) -> tuple[list[str], list[float | None]]:
    """Regional mean of a per-month aggregate (sum or mean) for each month from
    Jan start_year to the month of `end`, as parallel lists (one getInfo each)."""
    col = ee.ImageCollection(collection).select(band)
    n_months = (end.year - start_year) * 12 + end.month
    t0 = ee.Date.fromYMD(start_year, 1, 1)

    def month_feature(i):
        start = t0.advance(ee.Number(i), "month")
        stop = start.advance(1, "month")
        sub = col.filterDate(start, stop)
        img = ee.Image(ee.Algorithms.If(sub.size().gt(0), sub.sum() if reducer_per_month == "sum" else sub.mean(),
                                        ee.Image.constant(0).rename(band).updateMask(0)))
        val = img.reduceRegion(ee.Reducer.mean(), aoi, scale=scale_m, bestEffort=True, maxPixels=1e9).get(band)
        return ee.Feature(None, {"date": start.format("YYYY-MM"), "v": val})

    fc = ee.FeatureCollection(ee.List.sequence(0, n_months - 1).map(month_feature))
    dates = fc.aggregate_array("date").getInfo()
    values = [v if v is not None else None for v in fc.aggregate_array("v").getInfo()]
    # aggregate_array drops nulls, so re-align by date when lengths differ
    if len(values) != len(dates):
        by_date = {f["properties"]["date"]: f["properties"].get("v") for f in fc.getInfo()["features"]}
        values = [by_date.get(d) for d in dates]
    return dates, values


def _trim_trailing_none(dates: list[str], values: list[float | None]) -> tuple[list[str], list[float | None]]:
    while values and values[-1] is None:
        dates, values = dates[:-1], values[:-1]
    return dates, values


# --- SPI / SPEI ----------------------------------------------------------------------------

def spi_spei(region: Region, end: date) -> dict[str, Any]:
    init_ee()
    aoi = region_rectangle(region.bounds)
    p_dates, precip = _monthly_series(CHIRPS, "precipitation", aoi, CLIMATOLOGY_START, end, "sum", 5566)
    t_dates, temp_k = _monthly_series(ERA5L_MONTHLY, "temperature_2m", aoi, CLIMATOLOGY_START, end, "mean", 11132)
    p_dates, precip = _trim_trailing_none(p_dates, precip)
    t_dates, temp_k = _trim_trailing_none(t_dates, temp_k)
    n = min(len(precip), len(temp_k))
    dates = p_dates[:n]
    precip_arr = np.array([np.nan if v is None else v for v in precip[:n]], dtype=float)
    temp_c = np.array([np.nan if v is None else v - 273.15 for v in temp_k[:n]], dtype=float)

    lat = region.centroid[0]
    pet = indices.pet(temperature_celsius=temp_c, latitude_degrees=lat, data_start_year=CLIMATOLOGY_START)
    out: dict[str, Any] = {"through": dates[-1], "months": n}
    for scale in (3, 12):
        spi = indices.spi(precip_arr, scale=scale, distribution=indices.Distribution.gamma,
                          data_start_year=CLIMATOLOGY_START, calibration_year_initial=CALIBRATION[0],
                          calibration_year_final=CALIBRATION[1], periodicity=compute.Periodicity.monthly)
        spei = indices.spei(precips_mm=precip_arr, pet_mm=pet, scale=scale, distribution=indices.Distribution.gamma,
                            periodicity=compute.Periodicity.monthly, data_start_year=CLIMATOLOGY_START,
                            calibration_year_initial=CALIBRATION[0], calibration_year_final=CALIBRATION[1])
        out[f"spi{scale}"] = None if np.isnan(spi[n - 1]) else round(float(spi[n - 1]), 3)
        out[f"spei{scale}"] = None if np.isnan(spei[n - 1]) else round(float(spei[n - 1]), 3)
        out[f"spi{scale}_series"] = [None if np.isnan(v) else round(float(v), 3) for v in spi[-24:]]
    out["precip_mm_last12"] = [None if np.isnan(v) else round(float(v), 1) for v in precip_arr[-12:]]
    out["dates_last24"] = dates[-24:]
    return out


# --- VHI --------------------------------------------------------------------------------------

def vhi(region: Region, year: int, month: int) -> dict[str, Any]:
    """VCI/TCI/VHI for a calendar month vs. the 2001-> climatology of that month."""
    init_ee()
    aoi = region_rectangle(region.bounds)
    start = ee.Date.fromYMD(year, month, 1)
    stop = start.advance(1, "month")
    ndvi = ee.ImageCollection(MOD13).select("NDVI")
    lst = ee.ImageCollection(MOD11).select("LST_Day_1km")

    clim_ndvi = ndvi.filter(ee.Filter.calendarRange(month, month, "month")).filterDate(f"{MODIS_START}-01-01", stop)
    clim_lst = lst.filter(ee.Filter.calendarRange(month, month, "month")).filterDate(f"{MODIS_START}-01-01", stop)
    cur_ndvi = ndvi.filterDate(start, stop)
    cur_lst = lst.filterDate(start, stop)
    n_cur = cur_ndvi.size().getInfo()
    if n_cur == 0:
        return {"year": year, "month": month, "vhi": None, "vci": None, "tci": None, "composites": 0}

    ndvi_min, ndvi_max, ndvi_now = clim_ndvi.min(), clim_ndvi.max(), cur_ndvi.mean()
    lst_min, lst_max, lst_now = clim_lst.min(), clim_lst.max(), cur_lst.mean()
    vci = ndvi_now.subtract(ndvi_min).divide(ndvi_max.subtract(ndvi_min)).multiply(100).rename("vci")
    tci = lst_max.subtract(lst_now).divide(lst_max.subtract(lst_min)).multiply(100).rename("tci")
    vhi_img = vci.multiply(0.5).add(tci.multiply(0.5)).rename("vhi")
    stats = (
        ee.Image.cat([vci, tci, vhi_img])
        .reduceRegion(ee.Reducer.mean(), aoi, scale=1000, bestEffort=True, maxPixels=1e9)
        .getInfo()
    )
    return {
        "year": year,
        "month": month,
        "vci": None if stats.get("vci") is None else round(stats["vci"], 1),
        "tci": None if stats.get("tci") is None else round(stats["tci"], 1),
        "vhi": None if stats.get("vhi") is None else round(stats["vhi"], 1),
        "composites": n_cur,
    }


# --- SMAP soil moisture -------------------------------------------------------------------------

def smap_anomaly(region: Region, year: int, month: int) -> dict[str, Any]:
    """Root-zone soil moisture for the month vs. the same-month climatology (z-score)."""
    init_ee()
    aoi = region_rectangle(region.bounds)
    col = ee.ImageCollection(SMAP).select("sm_rootzone")

    def month_mean(y):
        y = ee.Number(y)
        start = ee.Date.fromYMD(y, month, 1)
        sub = col.filterDate(start, start.advance(1, "month"))
        val = ee.Algorithms.If(
            sub.size().gt(0),
            sub.mean().reduceRegion(ee.Reducer.mean(), aoi, scale=10000, bestEffort=True).get("sm_rootzone"),
            None,
        )
        return ee.Feature(None, {"year": y, "sm": val})

    fc = ee.FeatureCollection(ee.List.sequence(SMAP_START, year).map(month_mean))
    feats = fc.getInfo()["features"]
    series = {int(f["properties"]["year"]): f["properties"].get("sm") for f in feats}
    current = series.get(year)
    clim = [v for y, v in series.items() if y != year and v is not None]
    if current is None or len(clim) < 3:
        return {"year": year, "month": month, "sm_rootzone": current, "sm_z": None, "climatology_years": len(clim)}
    mean, std = float(np.mean(clim)), float(np.std(clim, ddof=1))
    z = (current - mean) / std if std > 0 else 0.0
    return {
        "year": year,
        "month": month,
        "sm_rootzone": round(current, 4),
        "sm_climatology_mean": round(mean, 4),
        "sm_z": round(z, 3),
        "sm_anomaly_pct": round(100 * (current - mean) / mean, 1),
        "climatology_years": len(clim),
    }


# --- Combined Drought Indicator -----------------------------------------------------------------

def spi_category(v: float | None) -> int:
    if v is None:
        return 0
    return 4 if v <= -2 else 3 if v <= -1.5 else 2 if v <= -1 else 1 if v <= -0.5 else 0


def vhi_category(v: float | None) -> int:
    if v is None:
        return 0
    return 4 if v < 10 else 3 if v < 20 else 2 if v < 30 else 1 if v < 40 else 0


def sm_category(z: float | None) -> int:
    if z is None:
        return 0
    return 4 if z <= -2 else 3 if z <= -1.5 else 2 if z <= -1 else 1 if z <= -0.5 else 0


def combined_indicator(spi3: float | None, vhi_v: float | None, sm_z: float | None,
                       available: dict[str, bool]) -> dict[str, Any]:
    """Weighted average of ordinal categories (0 normal .. 4 extreme). Weights are
    renormalised over the indicators that are actually available."""
    weights = {"spi": 0.4, "vhi": 0.3, "sm": 0.3}
    cats = {"spi": spi_category(spi3), "vhi": vhi_category(vhi_v), "sm": sm_category(sm_z)}
    use = {k: w for k, w in weights.items() if available.get(k)}
    total = sum(use.values()) or 1.0
    score = sum(cats[k] * w for k, w in use.items()) / total
    idx = int(round(score))
    return {"score": round(score, 2), "category": CATEGORY_NAMES[idx], "component_categories": cats,
            "weights_used": use}


def compute_drought(region: Region, target: date | None = None) -> dict[str, Any]:
    """Full drought snapshot for the month containing `target` (default: last complete month)."""
    if target is None:
        target = date.today().replace(day=1) - timedelta(days=1)
    spx = spi_spei(region, target)
    # VHI/SMAP for the target month; fall back one month if MODIS composites aren't in yet
    y, m = target.year, target.month
    v = vhi(region, y, m)
    if v["vhi"] is None:
        pm = 12 if m == 1 else m - 1
        py = y - 1 if m == 1 else y
        v = vhi(region, py, pm)
    s = smap_anomaly(region, y, m)
    cdi = combined_indicator(spx.get("spi3"), v.get("vhi"), s.get("sm_z"),
                             {"spi": spx.get("spi3") is not None, "vhi": v.get("vhi") is not None,
                              "sm": s.get("sm_z") is not None})
    return {
        "region": region.name,
        "target_month": f"{y:04d}-{m:02d}",
        "spi": spx,
        "vhi": v,
        "soil_moisture": s,
        "composite": cdi,
    }
