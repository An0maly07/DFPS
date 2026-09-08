"""/api/risk-score (PLAN.md §4.2) — XGBoost + SHAP."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.models.features import FEATURE_COLUMNS, REGION_POINTS, live_features
from backend.models.xgboost_risk import ALERT_THRESHOLDS, load_model
from backend.services import open_meteo as om
from backend.services import supabase_client as db

router = APIRouter(prefix="/api", tags=["risk"])


class RiskRequest(BaseModel):
    district: str = "kolhapur-sangli"
    features: dict[str, float] | None = Field(
        None,
        description=f"Any of {FEATURE_COLUMNS}. Missing ones are filled from live Open-Meteo data for the district.",
    )
    persist: bool = Field(False, description="Insert the result into risk_scores")


def _region_or_404(district: str) -> dict[str, Any]:
    region = db.get_region(district)
    if region is None:
        raise HTTPException(404, f"unknown district '{district}'")
    return region


@router.post("/risk-score")
def risk_score(req: RiskRequest) -> dict[str, Any]:
    region = _region_or_404(req.district)
    points = REGION_POINTS.get(req.district)
    if points is None:
        raise HTTPException(422, f"no forecast points configured for '{req.district}' (flood model covers: {list(REGION_POINTS)})")

    try:
        model = load_model()
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))

    provided = {k: v for k, v in (req.features or {}).items() if k in FEATURE_COLUMNS}
    unknown = sorted(set(req.features or {}) - set(FEATURE_COLUMNS))
    if unknown:
        raise HTTPException(422, f"unknown features {unknown}; expected subset of {FEATURE_COLUMNS}")

    context: dict[str, Any] = {}
    if set(FEATURE_COLUMNS) - provided.keys():
        try:
            live_row, ctx = live_features(points)
        except om.OpenMeteoError as e:
            raise HTTPException(502, f"Open-Meteo unavailable: {e}")
        features = {**live_row, **provided}
        context = {"scored_for": ctx["scored_for"], "discharge_forecast_max3d": ctx["discharge_forecast_max3d"]}
        feature_source = "live" if not provided else "live+override"
    else:
        features = provided
        feature_source = "request"

    pred = model.predict(features)
    out = {
        **pred,
        "district": req.district,
        "feature_source": feature_source,
        "context": context,
        "alert_thresholds": dict(ALERT_THRESHOLDS),
        "explanation": _explain(pred["shap_values"]),
    }
    if req.persist:
        saved = db.insert_risk_score(
            {
                "region_id": region["id"],
                "rainfall_mm": features.get("rain_mm"),
                "soil_moisture_pct": features.get("soil_moisture_pct"),
                "temperature_c": features.get("temperature_c"),
                "discharge_forecast": context.get("discharge_forecast_max3d", features.get("discharge")),
                "risk_score": pred["risk_score"],
                "alert_level": pred["alert_level"],
                "shap_json": {
                    "values": pred["shap_values"],
                    "base_value": pred["shap_base_value"],
                    "units": pred["shap_units"],
                    "features": pred["features"],
                    "label_source": pred["label_source"],
                    "model_version": pred["model_version"],
                    "feature_source": feature_source,
                    **context,
                },
            }
        )
        out["persisted"] = {"id": saved["id"], "scored_at": saved["scored_at"]}
    return out


def _explain(shap_values: dict[str, float], top: int = 3) -> dict[str, Any]:
    ranked = sorted(shap_values.items(), key=lambda kv: -abs(kv[1]))
    return {
        "pushing_up": [{"feature": f, "contribution": v} for f, v in ranked if v > 0][:top],
        "pushing_down": [{"feature": f, "contribution": v} for f, v in ranked if v < 0][:top],
    }


@router.get("/risk-score/latest")
def latest(district: str = Query("kolhapur-sangli")) -> dict[str, Any]:
    region = _region_or_404(district)
    row = db.latest_risk_score(region["id"])
    if row is None:
        raise HTTPException(404, f"no risk scores stored for {district} yet — POST /api/risk-score with persist=true")
    shap = row.get("shap_json") or {}
    return {**row, "district": district, "explanation": _explain(shap.get("values", {})) if shap.get("values") else None,
            "alert_thresholds": dict(ALERT_THRESHOLDS)}


@router.get("/risk-score/history")
def history(district: str = Query("kolhapur-sangli"), limit: int = Query(30, ge=1, le=500)) -> dict[str, Any]:
    region = _region_or_404(district)
    return {"district": district, "rows": db.risk_score_history(region["id"], limit)}


@router.get("/risk-score/model")
def model_info() -> dict[str, Any]:
    try:
        return load_model().meta
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))
