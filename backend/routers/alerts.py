"""/api/alerts/subscribe and /api/alerts/trigger (PLAN.md §4.2, §8)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from backend.config import settings
from backend.models.xgboost_risk import ALERT_THRESHOLDS
from backend.services import notify
from backend.services import supabase_client as db

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

LEVEL_ORDER = [name for name, _ in ALERT_THRESHOLDS]
FIRE_AT = "orange"


class SubscribeRequest(BaseModel):
    district: str = "kolhapur-sangli"
    channel: Literal["fcm", "whatsapp"]
    identifier: str = Field(..., min_length=6, description="FCM device token or E.164 phone (+91...)")


class TriggerRequest(BaseModel):
    district: str = "kolhapur-sangli"
    alert_level: str | None = Field(None, description="override; default = latest stored risk score")
    risk_score: float | None = None
    message: str | None = None
    dry_run: bool = False
    force: bool = Field(False, description="send even if level is below orange (demo)")


def _region_or_404(district: str) -> dict[str, Any]:
    region = db.get_region(district)
    if region is None:
        raise HTTPException(404, f"unknown district '{district}'")
    return region


def _check_internal(token: str | None) -> None:
    if settings.internal_api_token and token != settings.internal_api_token:
        raise HTTPException(401, "invalid or missing X-Internal-Token")


@router.post("/subscribe")
def subscribe(req: SubscribeRequest) -> dict[str, Any]:
    region = _region_or_404(req.district)
    row = db.upsert_subscriber(region["id"], req.channel, req.identifier)
    return {
        "id": row["id"],
        "district": req.district,
        "channel": req.channel,
        "identifier": notify._mask(req.identifier),
        "channel_configured": req.channel in notify.channels_configured(),
    }


@router.get("/subscribers")
def subscribers(district: str = Query("kolhapur-sangli")) -> dict[str, Any]:
    region = _region_or_404(district)
    rows = db.list_subscribers(region["id"])
    return {
        "district": district,
        "count": len(rows),
        "channels_configured": notify.channels_configured(),
        "subscribers": [{**r, "identifier": notify._mask(r["identifier"])} for r in rows],
    }


@router.post("/trigger")
def trigger(req: TriggerRequest, x_internal_token: str | None = Header(None)) -> dict[str, Any]:
    """Internal: fire notifications when the region's alert level is >= orange."""
    _check_internal(x_internal_token)
    region = _region_or_404(req.district)

    level, score = req.alert_level, req.risk_score
    if level is None:
        latest = db.latest_risk_score(region["id"])
        if latest is None:
            raise HTTPException(404, "no stored risk score to trigger from; pass alert_level explicitly")
        level, score = latest["alert_level"], latest["risk_score"]
    if level not in LEVEL_ORDER:
        raise HTTPException(422, f"alert_level must be one of {LEVEL_ORDER}")

    should_fire = LEVEL_ORDER.index(level) >= LEVEL_ORDER.index(FIRE_AT) or req.force
    if not should_fire:
        return {"fired": False, "district": req.district, "alert_level": level, "risk_score": score,
                "reason": f"level below {FIRE_AT}"}

    subs = db.list_subscribers(region["id"])
    title = f"[Horizon] {level.upper()} flood alert — {req.district}"
    body = req.message or (
        f"Flood risk for {req.district} is {level.upper()}"
        + (f" (score {score:.2f})" if score is not None else "")
        + ". Check the dashboard for the SAR flood extent and forecast."
    )
    results = notify.dispatch(subs, title, body, dry_run=req.dry_run)
    return {
        "fired": True,
        "district": req.district,
        "alert_level": level,
        "risk_score": score,
        "title": title,
        "body": body,
        "channels_configured": notify.channels_configured(),
        "subscribers": len(subs),
        "results": results,
        "sent": sum(r["status"] == "sent" for r in results),
    }
