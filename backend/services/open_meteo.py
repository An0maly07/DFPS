"""Open-Meteo client: weather forecast, ERA5 archive, and GloFAS river discharge.

All three endpoints are free and keyless. Responses are normalised into a flat
{"dates": [...], "<variable>": [...]} shape so routers and the risk engine don't
have to know the upstream JSON layout. Successful responses are cached on disk
(demo resilience — PLAN.md §7 pre-demo checklist).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

from backend.config import CACHE_DIR

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"

TIMEOUT_S = 30
RETRIES = 3

DAILY_WEATHER_VARS = [
    "precipitation_sum",
    "rain_sum",
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "et0_fao_evapotranspiration",
]
# Forecast and archive models expose soil-moisture at different depth bands, so the
# top-~30 cm layer is averaged from whichever bands each endpoint provides.
FORECAST_SOIL_VARS = ["soil_moisture_3_to_9cm", "soil_moisture_9_to_27cm"]
ARCHIVE_SOIL_VARS = ["soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm"]

DISCHARGE_VARS = [
    "river_discharge",
    "river_discharge_mean",
    "river_discharge_median",
    "river_discharge_max",
    "river_discharge_min",
    "river_discharge_p25",
    "river_discharge_p75",
]


class OpenMeteoError(RuntimeError):
    pass


def _cache_path(url: str, params: dict[str, Any]) -> Path:
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    return CACHE_DIR / f"open_meteo_{key}.json"


def _get(url: str, params: dict[str, Any], use_cache: bool) -> dict[str, Any]:
    cache_file = _cache_path(url, params)
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    last_err: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT_S)
            if r.status_code == 200:
                payload = r.json()
                if "error" in payload and payload.get("error"):
                    raise OpenMeteoError(payload.get("reason", "unknown Open-Meteo error"))
                cache_file.write_text(json.dumps(payload))
                return payload
            if r.status_code == 400:
                # Bad parameters won't get better on retry.
                raise OpenMeteoError(f"{url} 400: {r.text[:300]}")
            last_err = OpenMeteoError(f"{url} {r.status_code}: {r.text[:300]}")
        except requests.RequestException as e:
            last_err = e
        time.sleep(1.5 * (attempt + 1))

    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())
    raise OpenMeteoError(f"Open-Meteo request failed after {RETRIES} attempts: {last_err}")


def _daily_mean_of_hourly(hourly: dict[str, Any], vars_: list[str]) -> list[float | None]:
    """Collapse hourly soil-moisture bands into one daily mean (m³/m³ → %)."""
    times: list[str] = hourly.get("time", [])
    per_day: dict[str, list[float]] = {}
    for i, t in enumerate(times):
        vals = [hourly[v][i] for v in vars_ if v in hourly and hourly[v][i] is not None]
        if vals:
            per_day.setdefault(t[:10], []).append(sum(vals) / len(vals))
    days = sorted(per_day)
    return [round(100.0 * sum(per_day[d]) / len(per_day[d]), 2) for d in days]


def _normalise_weather(payload: dict[str, Any], soil_vars: list[str]) -> dict[str, Any]:
    daily = payload.get("daily", {})
    out: dict[str, Any] = {
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "elevation": payload.get("elevation"),
        "timezone": payload.get("timezone"),
        "dates": daily.get("time", []),
    }
    for v in DAILY_WEATHER_VARS:
        out[v] = daily.get(v, [])
    hourly = payload.get("hourly", {})
    sm = _daily_mean_of_hourly(hourly, soil_vars) if hourly else []
    # Align soil moisture to the daily date axis (hourly and daily share the range).
    out["soil_moisture_pct"] = sm[: len(out["dates"])] if sm else [None] * len(out["dates"])
    return out


def get_weather_forecast(
    lat: float,
    lon: float,
    forecast_days: int = 7,
    past_days: int = 7,
    use_cache: bool = False,
) -> dict[str, Any]:
    """Daily rainfall / temperature / ET0 + top-layer soil moisture (forecast model)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join(DAILY_WEATHER_VARS),
        "hourly": ",".join(FORECAST_SOIL_VARS),
        "timezone": "Asia/Kolkata",
        "forecast_days": forecast_days,
        "past_days": past_days,
    }
    return _normalise_weather(_get(FORECAST_URL, params, use_cache), FORECAST_SOIL_VARS)


def get_weather_archive(
    lat: float,
    lon: float,
    start: date | str,
    end: date | str,
    use_cache: bool = True,
) -> dict[str, Any]:
    """ERA5 reanalysis for a historical window (used for training + event replay)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": str(start),
        "end_date": str(end),
        "daily": ",".join(DAILY_WEATHER_VARS),
        "hourly": ",".join(ARCHIVE_SOIL_VARS),
        "timezone": "Asia/Kolkata",
    }
    return _normalise_weather(_get(ARCHIVE_URL, params, use_cache), ARCHIVE_SOIL_VARS)


def get_flood_forecast(
    lat: float,
    lon: float,
    forecast_days: int = 7,
    past_days: int = 7,
    start: date | str | None = None,
    end: date | str | None = None,
    use_cache: bool | None = None,
) -> dict[str, Any]:
    """GloFAS river discharge (m³/s). Pass start/end for a historical window instead
    of forecast_days/past_days (historical windows are cached by default; live
    forecasts are not). Ensemble statistics (mean/median/p25/p75/min/max) are only
    populated for the forecast horizon."""
    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join(DISCHARGE_VARS),
    }
    historical = bool(start and end)
    if use_cache is None:
        use_cache = historical
    if historical:
        params["start_date"] = str(start)
        params["end_date"] = str(end)
    else:
        params["forecast_days"] = forecast_days
        params["past_days"] = past_days
    payload = _get(FLOOD_URL, params, use_cache)
    daily = payload.get("daily", {})
    out: dict[str, Any] = {
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "dates": daily.get("time", []),
    }
    for v in DISCHARGE_VARS:
        out[v] = daily.get(v, [])
    return out
