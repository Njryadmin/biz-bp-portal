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

v1 → v2 升级 (2026-09-04): 用 ``check_domain_access(FINANCE, PROJECT)``
替代 v1 ``require_business_line`` (后者只判断 line 范围,不区分数据域).
财务预测涉及财务+项目指标,这两个域中任一允许即可 (any-of 语义).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core.auth_v2 import CurrentUserV2, get_current_user_v2
from ..core.logging import get_logger
from ..core.rbac_v2 import (
    DataDomain,
    check_domain_access,
    filter_accessible_lines_v2,
)
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
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> dict:
    line_ids = list_profiles()
    allowed = filter_accessible_lines_v2(user, line_ids)
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
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> dict:
    # 预测 profile 涉及 finance + project 域,any-of 即可
    await check_domain_access(
        user, line_id, [DataDomain.FINANCE, DataDomain.PROJECT], write=False
    )
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
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> ForecastResult:
    # run 是写操作 (生成预测结果)
    await check_domain_access(
        user, req.line_id, [DataDomain.FINANCE, DataDomain.PROJECT], write=True
    )
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
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> ActualVsForecastResult:
    # compare 也是写操作 (实际 vs 预测的 delta 计算)
    await check_domain_access(
        user, req.line_id, [DataDomain.FINANCE, DataDomain.PROJECT], write=True
    )
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
