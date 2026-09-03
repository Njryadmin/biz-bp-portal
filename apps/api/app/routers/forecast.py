"""
apps/api/app/routers/forecast.py

Cross-business-line Rolling Forecast router.

Mounted at /api/forecast by `app.main` (NOT via the business-line
auto-discovery path, since the forecast engine is universal — it spans
all business lines).

Endpoints:

* GET  /profiles                  — list line profiles the user can access
* GET  /profiles/{line_id}        — full profile for one line (RBAC)
* POST /run                       — run a forecast; line access required
* POST /compare                   — actual vs forecast; line access required

The engine itself lives in `app.services.forecast_engine`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core.auth import CurrentUser, get_current_user
from ..core.logging import get_logger
from ..core.rbac import filter_accessible_lines, require_business_line
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
    summary="List forecast profiles for business lines the user can access",
)
async def list_profiles_endpoint(
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    line_ids = list_profiles()
    allowed = filter_accessible_lines(user, line_ids)
    return {
        "count": len(allowed),
        "profiles": [
            {"line_id": lid, **_profile_summary(lid)} for lid in allowed
        ],
    }


@router.get(
    "/profiles/{line_id}",
    summary="Get the full forecast profile for one business line (RBAC)",
)
async def get_profile_endpoint(
    line_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    await require_business_line(line_id, user)
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
    summary="Run a rolling forecast for one KPI series (RBAC: line access)",
)
async def run_endpoint(
    req: ForecastRequest,
    user: CurrentUser = Depends(get_current_user),
) -> ForecastResult:
    await require_business_line(req.line_id, user, require_write=True)
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
    summary="Compare recent actuals vs the model's prediction (RBAC: line access)",
)
async def compare_endpoint(
    req: ActualVsForecastRequest,
    user: CurrentUser = Depends(get_current_user),
) -> ActualVsForecastResult:
    await require_business_line(req.line_id, user, require_write=True)
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
