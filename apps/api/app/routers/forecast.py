"""
apps/api/app/routers/forecast.py

Cross-business-line Rolling Forecast router.

Mounted at /api/forecast by `app.main` (NOT via the business-line
auto-discovery path, since the forecast engine is universal — it spans
all business lines).

Endpoints:

* GET  /profiles                  — list all line profiles (summary)
* GET  /profiles/{line_id}        — full profile for one line
* POST /run                       — run a forecast; returns ForecastResult
* POST /compare                   — actual vs forecast (mock comparison)

The engine itself lives in `app.services.forecast_engine`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..core.logging import get_logger
from ..services.forecast_engine import (
    ActualVsForecastRequest,
    ActualVsForecastResult,
    ForecastRequest,
    ForecastResult,
    list_profiles,
    load_profile,
    run_compare,
    run_forecast,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


# ─────────────────────────────────────────────────────────────────────────
# Profile endpoints
# ─────────────────────────────────────────────────────────────────────────


def _profile_summary(line_id: str) -> dict:
    p = load_profile(line_id)
    return {
        "line_id": p.line_id,
        "line_name": p.line_name or p.line_id,
        "series_count": len(p.series),
        "attribution_count": len(p.attribution),
        "series": [
            {
                "indicator_id": s.indicator_id,
                "name": s.name or s.indicator_id,
                "frequency": s.frequency,
                "method": s.method,
                "horizon_months": s.horizon_months,
                "historical_periods": s.historical_periods,
            }
            for s in p.series
        ],
        "attribution": [
            {
                "id": a.id,
                "name": a.name or a.id,
                "driver_count": len(a.drivers),
            }
            for a in p.attribution
        ],
    }


@router.get(
    "/profiles",
    summary="List forecast profiles for all business lines that have one",
)
async def list_profiles_endpoint() -> dict:
    line_ids = list_profiles()
    return {
        "count": len(line_ids),
        "profiles": [
            {"line_id": lid, **_profile_summary(lid)} for lid in line_ids
        ],
    }


@router.get(
    "/profiles/{line_id}",
    summary="Get the full forecast profile for one business line",
)
async def get_profile_endpoint(line_id: str) -> dict:
    try:
        p = load_profile(line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "line_id": p.line_id,
        "line_name": p.line_name or p.line_id,
        "series": [s.model_dump() for s in p.series],
        "attribution": [a.model_dump() for a in p.attribution],
    }


# ─────────────────────────────────────────────────────────────────────────
# Run endpoint
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/run",
    response_model=ForecastResult,
    summary="Run a rolling forecast for one KPI series",
)
async def run_endpoint(req: ForecastRequest) -> ForecastResult:
    try:
        profile = load_profile(req.line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return run_forecast(profile, req)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"bad request: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────
# Compare endpoint
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/compare",
    response_model=ActualVsForecastResult,
    summary="Compare recent actuals vs the model's prediction (mock)",
)
async def compare_endpoint(req: ActualVsForecastRequest) -> ActualVsForecastResult:
    try:
        profile = load_profile(req.line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return run_compare(profile, req)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"bad request: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
