"""Indradhanu API — FastAPI entrypoint (PLAN.md §4).

Run from the repo root:  uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.routers import alerts, drought, events, flood, risk

log = logging.getLogger("indradhanu")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the model so the first /api/risk-score call doesn't pay the load cost.
    try:
        from backend.models.xgboost_risk import load_model

        load_model()
        log.info("risk model loaded")
    except FileNotFoundError as e:
        log.warning("risk model not available: %s", e)
    yield


app = FastAPI(
    title="Indradhanu — Drought & Flood Risk API",
    version="0.1.0",
    description="Sentinel-1 SAR flood extent, XGBoost+SHAP risk scoring, GloFAS forecasts, drought indices.",
    lifespan=lifespan,
)


# Starlette runs `Exception` handlers in the outermost ServerErrorMiddleware, i.e.
# outside CORS, so a crash reaches the browser as a CORS failure. This middleware is
# added before CORS (hence sits inside it) and turns crashes into JSON 500s that
# carry CORS headers, so the frontend sees the real error.
@app.middleware("http")
async def json_500(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:  # deliberately broad: last line of defence
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {str(exc)[:300]}"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(flood.router)
app.include_router(risk.router)
app.include_router(drought.router)
app.include_router(alerts.router)
app.include_router(events.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    from backend.models.xgboost_risk import MODEL_PATH
    from backend.services.notify import channels_configured

    return {
        "status": "ok",
        "model_loaded": MODEL_PATH.exists(),
        "titiler_url": settings.titiler_url,
        "alert_channels": channels_configured(),
    }
