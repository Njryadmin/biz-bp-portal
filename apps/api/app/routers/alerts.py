"""
apps/api/app/routers/alerts.py

Cross-business-line Alert Center router.

Mounted at /api/alerts by `app.main` (NOT via the business-line
auto-discovery path, since the alert engine is universal — it spans
all business lines).

Endpoints:

* GET  /rules/{line_id}                — list all rules for a line (RBAC)
* GET  /rules/{line_id}/summary        — rule summary (counts by severity)
* GET  /profiles                      — list line profiles (filtered)
* POST /check                          — run a check; line access required
* GET  /history                        — recent triggered alerts (paginated)
* POST /acknowledge/{alert_id}         — mark one alert as acknowledged
                                          (RBAC: line write)
* DELETE /{alert_id}                   — soft-delete an alert (RBAC: line
                                          write)

The engine itself lives in `app.services.alert_engine`.

v1 → v2 升级 (2026-09-04): 用 ``check_domain_access(BUSINESS)`` 替代
v1 ``require_business_line``. 告警是业务指标监控,归 BUSINESS 域.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.auth_v2 import CurrentUserV2, get_current_user_v2
from ..core.logging import get_logger
from ..core.rbac_v2 import (
    DataDomain,
    check_domain_access,
    filter_accessible_lines_v2,
)
from ..services.alert_engine import (
    AlertCheckRequest,
    AlertCheckResult,
    AlertHistoryResponse,
    AlertProfile,
    TriggeredAlert,
    acknowledge,
    check,
    delete,
    get_alert,
    history,
    list_profiles,
    load_profile,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


# ─────────────────────────────────────────────────────────────────────────
# Rule list / summary
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/rules/{line_id}",
    summary="List alert rules for a business line (RBAC)",
)
async def list_rules_endpoint(
    line_id: str,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> dict:
    # 列规则 = 读
    await check_domain_access(user, line_id, DataDomain.BUSINESS, write=False)
    try:
        p = load_profile(line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "line_id": p.line_id,
        "line_name": p.line_name or p.line_id,
        "rule_count": len(p.rules),
        "rules": [r.model_dump() for r in p.rules],
        "attribution": [a.model_dump() for a in p.attribution],
    }


@router.get(
    "/rules/{line_id}/summary",
    summary="Rule summary: counts by severity + enabled/disabled (RBAC)",
)
async def rules_summary_endpoint(
    line_id: str,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> dict:
    # 列 summary = 读
    await check_domain_access(user, line_id, DataDomain.BUSINESS, write=False)
    try:
        p = load_profile(line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    by_sev: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    enabled = 0
    disabled = 0
    for r in p.rules:
        by_sev[r.severity] = by_sev.get(r.severity, 0) + 1
        if r.enabled:
            enabled += 1
        else:
            disabled += 1
    return {
        "line_id": p.line_id,
        "line_name": p.line_name or p.line_id,
        "total_rules": len(p.rules),
        "enabled": enabled,
        "disabled": disabled,
        "by_severity": by_sev,
    }


@router.get(
    "/profiles",
    summary="List alert profiles for business lines the user can access",
)
async def list_profiles_endpoint(
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> dict:
    line_ids = list_profiles()
    allowed = filter_accessible_lines_v2(user, line_ids)
    return {
        "count": len(allowed),
        "lines": [
            {
                "line_id": lid,
                "rule_count": len(load_profile(lid).rules),
            }
            for lid in allowed
        ],
    }


# ─────────────────────────────────────────────────────────────────────────
# Check
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/check",
    response_model=AlertCheckResult,
    summary="Run a check; returns triggered alerts (RBAC: line write)",
)
async def check_endpoint(
    req: AlertCheckRequest,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> AlertCheckResult:
    # check 触发评估 = 写
    await check_domain_access(user, req.line_id, DataDomain.BUSINESS, write=True)
    try:
        profile = load_profile(req.line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return check(profile, req)


# ─────────────────────────────────────────────────────────────────────────
# History (read-only, filtered by accessible lines)
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/history",
    response_model=AlertHistoryResponse,
    summary="Recent triggered alerts (newest first), paginated; filtered to accessible lines",
)
async def history_endpoint(
    line_id: str | None = Query(None, description="filter by business line id"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> AlertHistoryResponse:
    # If a specific line is requested, require line access; otherwise
    # the engine filters to the union of accessible lines.
    if line_id is not None:
        await check_domain_access(user, line_id, DataDomain.BUSINESS, write=False)
        items, total = history(line_id, limit, offset)
        return AlertHistoryResponse(
            line_id=line_id, total=total, limit=limit, offset=offset, items=items
        )
    # No line filter — restrict to the union of accessible lines.
    allowed = filter_accessible_lines_v2(user, list_profiles())
    if not allowed:
        return AlertHistoryResponse(
            line_id=None, total=0, limit=limit, offset=offset, items=[]
        )
    items_all: list = []
    total_all = 0
    for lid in allowed:
        its, tot = history(lid, limit, offset)
        items_all.extend(its)
        total_all += tot
    # sort newest first by triggered_at
    items_all.sort(
        key=lambda x: getattr(x, "triggered_at", None) or "", reverse=True
    )
    items_all = items_all[:limit]
    return AlertHistoryResponse(
        line_id=None, total=total_all, limit=limit, offset=offset, items=items_all
    )


# ─────────────────────────────────────────────────────────────────────────
# Acknowledge / delete
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/acknowledge/{alert_id}",
    response_model=TriggeredAlert,
    summary="Mark an alert as acknowledged (RBAC: line write)",
)
async def acknowledge_endpoint(
    alert_id: str,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> TriggeredAlert:
    # Look up the alert to know its line_id, then check write access.
    alert = get_alert(alert_id)
    if alert is None:
        raise HTTPException(
            status_code=404, detail=f"alert not found: {alert_id}"
        )
    # acknowledge 是写 (修改 alert 状态)
    await check_domain_access(
        user, alert.line_id, DataDomain.BUSINESS, write=True
    )
    acked = acknowledge(alert_id)
    if acked is None:
        raise HTTPException(status_code=404, detail=f"alert not found: {alert_id}")
    return acked


@router.delete(
    "/{alert_id}",
    summary="Soft-delete (resolve / ignore) an alert (RBAC: line write)",
)
async def delete_endpoint(
    alert_id: str,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> dict:
    alert = get_alert(alert_id)
    if alert is None:
        raise HTTPException(
            status_code=404, detail=f"alert not found: {alert_id}"
        )
    # delete 是写
    await check_domain_access(
        user, alert.line_id, DataDomain.BUSINESS, write=True
    )
    ok = delete(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"alert not found: {alert_id}")
    return {"deleted": alert_id}
