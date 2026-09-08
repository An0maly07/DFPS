"""/api/events/replay (PLAN.md §4.2, §9)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.models.replay import EVENTS, build_replay

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
def list_events() -> dict[str, Any]:
    return {"events": [{"id": k, **{x: v[x] for x in ("type", "region", "title", "start", "end")}} for k, v in EVENTS.items()]}


@router.get("/replay")
def replay(event: str = Query("maharashtra_2021"), refresh: bool = Query(False)) -> dict[str, Any]:
    try:
        return build_replay(event, refresh=refresh)
    except KeyError:
        raise HTTPException(404, f"unknown event '{event}'; known: {sorted(EVENTS)}")
    except FileNotFoundError as e:
        raise HTTPException(503, f"replay inputs missing: {e}")
