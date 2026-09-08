"""Historical event replay (PLAN.md §4.2 /api/events/replay, §9).

For a flood event: daily features from the training dataset (ERA5 + GloFAS
reanalysis) are scored by the *trained* model to show what it would have said each
day, joined with the SAR flood-extent snapshots stored in Supabase. For a drought
event: the monthly drought_index rows in the window. Results are cached as JSON.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from backend.config import DATA_DIR
from backend.models.features import FEATURE_COLUMNS
from backend.models.xgboost_risk import ALERT_THRESHOLDS, alert_level, load_model
from backend.services import supabase_client as db

REPLAY_DIR = DATA_DIR / "replay"
TRAIN_DIR = DATA_DIR / "training"

EVENTS: dict[str, dict[str, Any]] = {
    "maharashtra_2021": {
        "type": "flood",
        "region": "kolhapur-sangli",
        "title": "July 2021 Kolhapur–Sangli floods",
        "start": "2021-07-01",
        "end": "2021-08-31",
        "documented_peak": "2021-07-23",
        "narrative": (
            "Extreme Western Ghats rainfall on 21–23 July 2021 pushed the Panchganga and "
            "Krishna over danger level; Kolhapur, Ichalkaranji, Shirol and Sangli were inundated "
            "and >200,000 people were evacuated across the two districts."
        ),
        "sources": [
            "IMD Pune monsoon 2021 report (Mahabaleshwar ~600 mm on 22–23 Jul)",
            "Maharashtra SDMA situation reports, 23–27 Jul 2021",
        ],
    },
    "marathwada_2018": {
        "type": "drought",
        "region": "marathwada-beed",
        "title": "2018–19 Marathwada drought (Beed)",
        "start": "2018-06-01",
        "end": "2019-06-30",
        "narrative": (
            "Deficient 2018 monsoon over Marathwada; Maharashtra declared drought in 151 talukas "
            "on 31 Oct 2018 with Beed among the worst affected."
        ),
        "sources": ["Government of Maharashtra GR, 31 Oct 2018", "IMD district rainfall statistics 2018"],
    },
}


def _cache_path(event: str) -> "Path":
    from pathlib import Path

    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    return Path(REPLAY_DIR / f"{event}.json")


def _flood_replay(event: str, spec: dict[str, Any]) -> dict[str, Any]:
    csv = TRAIN_DIR / f"{spec['region']}_risk_features.csv"
    df = pd.read_csv(csv, index_col=0, parse_dates=True).loc[spec["start"]: spec["end"]]
    model = load_model()
    X = df[FEATURE_COLUMNS].astype(float)
    proba = model.model.predict_proba(X)[:, 1]
    shap = np.asarray(model.explainer.shap_values(X))

    region = db.get_region(spec["region"])
    extents = db.get_flood_extents(region["id"], "sar_otsu") if region else []
    by_day = {e["captured_at"][:10]: e for e in extents}

    from backend.routers.flood import tile_templates  # tile URL builder

    timeline = []
    for i, (day, row) in enumerate(df.iterrows()):
        d = day.date().isoformat()
        entry = {
            "date": d,
            "rain_mm": round(float(row["rain_mm"]), 1),
            "rain_3d": round(float(row["rain_3d"]), 1),
            "upstream_rain_7d": round(float(row["upstream_rain_7d"]), 1),
            "soil_moisture_pct": round(float(row["soil_moisture_pct"]), 1),
            "discharge": round(float(row["discharge"]), 1),
            "risk_score": round(float(proba[i]), 4),
            "alert_level": alert_level(float(proba[i])),
            "shap_values": {f: round(float(v), 3) for f, v in zip(FEATURE_COLUMNS, shap[i])},
        }
        if d in by_day:
            e = by_day[d]
            meta = e["meta"] or {}
            entry["flood_extent"] = {
                "id": e["id"],
                "cog_url": e["cog_url"],
                "urls": meta.get("urls", {}),
                "tiles": tile_templates(meta.get("urls", {})),
                "flood_area_km2": meta.get("flood_area_km2"),
                "bounds": meta.get("bounds"),
            }
        timeline.append(entry)

    levels = [name for name, _ in ALERT_THRESHOLDS]
    peak = max(timeline, key=lambda t: t["discharge"])
    first_at = {}
    for lvl in ("yellow", "orange", "red"):
        hit = next((t for t in timeline if levels.index(t["alert_level"]) >= levels.index(lvl)), None)
        first_at[lvl] = hit["date"] if hit else None
    lead = (
        (datetime.fromisoformat(peak["date"]) - datetime.fromisoformat(first_at["orange"])).days
        if first_at["orange"] else None
    )
    return {
        "event": event,
        **{k: v for k, v in spec.items()},
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model_version": model.meta["model_version"],
        "label_source": model.meta["label_source"],
        "summary": {
            "glofas_peak_date": peak["date"],
            "glofas_peak_discharge": peak["discharge"],
            "first_yellow": first_at["yellow"],
            "first_orange": first_at["orange"],
            "first_red": first_at["red"],
            "lead_days_orange_before_peak": lead,
            "max_risk_score": max(t["risk_score"] for t in timeline),
            "sar_dates": sorted(by_day),
            "sar_flood_area_km2": {d: (e["meta"] or {}).get("flood_area_km2") for d, e in by_day.items()},
        },
        "timeline": timeline,
    }


def _drought_replay(event: str, spec: dict[str, Any]) -> dict[str, Any]:
    region = db.get_region(spec["region"])
    rows = db.get_drought_rows(region["id"], 240) if region else []
    rows = [r for r in rows if spec["start"][:7] <= r["recorded_at"][:7] <= spec["end"][:7]]
    rows.sort(key=lambda r: r["recorded_at"])
    timeline = [
        {
            "month": r["recorded_at"][:7],
            "spi3": r["spi"],
            "spei3": r["spei"],
            "vhi": r["vhi"],
            "sm_z": ((r.get("meta") or {}).get("soil_moisture") or {}).get("sm_z"),
            "composite_category": r["composite_category"],
        }
        for r in rows
    ]
    worst = min(timeline, key=lambda t: (t["spi3"] if t["spi3"] is not None else 0)) if timeline else None
    return {
        "event": event,
        **spec,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": {"months_available": len(timeline), "worst_month": worst},
        "timeline": timeline,
        "note": "Monthly snapshots are computed on demand (GET /api/drought-index?district=&month=&refresh=true); "
                "only the months already stored appear here.",
    }


def build_replay(event: str, refresh: bool = False) -> dict[str, Any]:
    if event not in EVENTS:
        raise KeyError(event)
    cache = _cache_path(event)
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    spec = EVENTS[event]
    out = _flood_replay(event, spec) if spec["type"] == "flood" else _drought_replay(event, spec)
    cache.write_text(json.dumps(out, indent=1))
    return out
