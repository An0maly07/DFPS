"""Feature assembly for the flood-risk model (PLAN.md §3.4).

One row per day for a region:
    rain_mm, rain_3d, rain_7d          local ERA5 rainfall and rolling totals
    upstream_rain_mm, upstream_rain_7d rainfall at the Western Ghats catchment point
    soil_moisture_pct                  local top-~30 cm volumetric soil moisture
    temperature_c                      local daily mean
    discharge                          GloFAS river discharge on the day (m³/s)
    discharge_trend                    discharge minus previous day
    doy_sin, doy_cos                   seasonality

Label (proxy — see xgboost_risk.py docstring):
    flood_next3d = 1 if max GloFAS discharge over days t+1..t+3 >= q_flood

Everything is pulled from Open-Meteo (ERA5 archive + GloFAS reanalysis) so the
same code serves training (2010→) and live scoring (today).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.services import open_meteo as om

FEATURE_COLUMNS = [
    "rain_mm",
    "rain_3d",
    "rain_7d",
    "upstream_rain_mm",
    "upstream_rain_7d",
    "soil_moisture_pct",
    "temperature_c",
    "discharge",
    "discharge_trend",
    "doy_sin",
    "doy_cos",
]
LABEL_COLUMN = "flood_next3d"
LEAD_DAYS = 3


@dataclass(frozen=True)
class RegionPoints:
    """Where to sample the point-based sources for a region."""

    name: str
    lat: float            # river/gauge point used for GloFAS + local weather
    lon: float
    upstream_lat: float   # catchment point that drives the river (Western Ghats)
    upstream_lon: float


# Kolhapur city on the Panchganga; upstream = Gaganbawda ghat, the wettest part of
# the Panchganga catchment (ERA5 cell distinct from the city cell).
KOLHAPUR_SANGLI = RegionPoints("kolhapur-sangli", 16.70, 74.24, 16.55, 73.85)

REGION_POINTS: dict[str, RegionPoints] = {KOLHAPUR_SANGLI.name: KOLHAPUR_SANGLI}


# --- raw series ----------------------------------------------------------------------

def _archive_daily(lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    """ERA5 archive, fetched year by year so each response stays small and cacheable."""
    frames = []
    y0, y1 = start.year, end.year
    for y in range(y0, y1 + 1):
        s = max(start, date(y, 1, 1))
        e = min(end, date(y, 12, 31))
        payload = om.get_weather_archive(lat, lon, s, e, use_cache=True)
        frames.append(
            pd.DataFrame(
                {
                    "date": pd.to_datetime(payload["dates"]),
                    "rain_mm": payload["precipitation_sum"],
                    "temperature_c": payload["temperature_2m_mean"],
                    "soil_moisture_pct": payload["soil_moisture_pct"],
                }
            )
        )
    df = pd.concat(frames, ignore_index=True).drop_duplicates("date").set_index("date").sort_index()
    return df


def _glofas_daily(lat: float, lon: float, start: date, end: date) -> pd.Series:
    payload = om.get_flood_forecast(lat, lon, start=start, end=end)
    s = pd.Series(payload["river_discharge"], index=pd.to_datetime(payload["dates"]), name="discharge")
    return s.astype(float)


# --- feature engineering ---------------------------------------------------------------

def engineer(local: pd.DataFrame, upstream_rain: pd.Series, discharge: pd.Series) -> pd.DataFrame:
    df = local.copy()
    df["upstream_rain_mm"] = upstream_rain.reindex(df.index)
    df["discharge"] = discharge.reindex(df.index)
    df["rain_3d"] = df["rain_mm"].rolling(3, min_periods=1).sum()
    df["rain_7d"] = df["rain_mm"].rolling(7, min_periods=1).sum()
    df["upstream_rain_7d"] = df["upstream_rain_mm"].rolling(7, min_periods=1).sum()
    df["discharge_trend"] = df["discharge"].diff().fillna(0.0)
    doy = df.index.dayofyear.to_numpy()
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    return df


def add_label(df: pd.DataFrame, q_flood: float, lead_days: int = LEAD_DAYS) -> pd.DataFrame:
    """flood_next3d = 1 if the max discharge over the next `lead_days` days >= q_flood."""
    q = df["discharge"]
    future_max = q[::-1].rolling(lead_days, min_periods=1).max()[::-1].shift(-1)
    df = df.copy()
    df[LABEL_COLUMN] = (future_max >= q_flood).astype(int)
    df.loc[future_max.isna(), LABEL_COLUMN] = np.nan
    return df


def return_period_flows(discharge: pd.Series) -> dict[str, float]:
    """Return-period flows from annual maxima using Weibull plotting positions.

    With ~15 years of GloFAS this is coarse, but it is the standard way to turn a
    discharge series into 'flood' thresholds without gauge records.
    """
    annual_max = discharge.groupby(discharge.index.year).max().dropna()
    complete_years = [y for y in annual_max.index if (discharge.index.year == y).sum() >= 300]
    annual_max = annual_max.loc[complete_years].sort_values()
    n = len(annual_max)
    ranks = np.arange(1, n + 1)
    exceed_prob = 1 - ranks / (n + 1)  # P(annual max >= x)
    rp = 1 / exceed_prob

    def flow_at(target_rp: float) -> float:
        return float(np.interp(target_rp, rp, annual_max.to_numpy()))

    return {"q_1_5yr": flow_at(1.5), "q_2yr": flow_at(2.0), "q_5yr": flow_at(5.0), "years": int(n)}


# --- public API -------------------------------------------------------------------------

def build_dataset(points: RegionPoints, start: date, end: date) -> tuple[pd.DataFrame, dict[str, float]]:
    """Full labelled daily dataset for [start, end]."""
    local = _archive_daily(points.lat, points.lon, start, end)
    upstream = _archive_daily(points.upstream_lat, points.upstream_lon, start, end)["rain_mm"]
    discharge = _glofas_daily(points.lat, points.lon, start, end)
    df = engineer(local, upstream, discharge)
    rp = return_period_flows(discharge)
    df = add_label(df, rp["q_2yr"])
    return df, rp


def live_features(points: RegionPoints, past_days: int = 10) -> tuple[dict[str, float], dict]:
    """Feature row for *today* from the forecast APIs (past_days of context for the
    rolling windows). Also returns the raw forecast payloads for the API layer."""
    weather = om.get_weather_forecast(points.lat, points.lon, forecast_days=7, past_days=past_days)
    upstream = om.get_weather_forecast(points.upstream_lat, points.upstream_lon, forecast_days=7, past_days=past_days)
    flood = om.get_flood_forecast(points.lat, points.lon, forecast_days=7, past_days=past_days)

    local = pd.DataFrame(
        {
            "date": pd.to_datetime(weather["dates"]),
            "rain_mm": weather["precipitation_sum"],
            "temperature_c": weather["temperature_2m_mean"],
            "soil_moisture_pct": weather["soil_moisture_pct"],
        }
    ).set_index("date")
    up = pd.Series(upstream["precipitation_sum"], index=pd.to_datetime(upstream["dates"]))
    q = pd.Series(flood["river_discharge"], index=pd.to_datetime(flood["dates"]), dtype=float)

    df = engineer(local, up, q)
    today = pd.Timestamp(date.today())
    if today not in df.index:
        today = df.index[df.index <= today][-1]
    row = df.loc[today, FEATURE_COLUMNS].astype(float).to_dict()

    horizon = q[(q.index > today) & (q.index <= today + timedelta(days=LEAD_DAYS))]
    context = {
        "scored_for": today.date().isoformat(),
        "discharge_forecast_max3d": float(horizon.max()) if len(horizon) else None,
        "weather": weather,
        "upstream": upstream,
        "flood": flood,
    }
    return row, context
