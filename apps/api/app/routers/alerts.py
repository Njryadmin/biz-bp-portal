"""
apps/api/app/routers/alerts.py

Cross-business-line Alert Center router.

Mounted at /api/alerts by `app.main` (NOT via the business-line
auto-discovery path, since the alert engine is universal — it spans
all business lines).

Endpoints:

* GET  /rules/{line_id}                — list all rules for a line
* GET  /rules/{line_id}/summary        — rule summary (counts by severity)
* POST /check                          — run a check; persist triggered alerts
* GET  /history                        — recent triggered alerts (paginated)
* POST /acknowledge/{alert_id}         — mark one alert as acknowledged
* DELETE /{alert_id}                   — soft-delete an alert

The engine itself lives in `app.services.alert_engine`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..core.logging import get_logger
from ..services.alert_engine import (
    AlertCheckRequest,
    AlertCheckResult,
    AlertHistoryResponse,
    AlertProfile,
    TriggeredAlert,
    acknowledge,
    check,
    delete,
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
    summary="List alert rules for a business line",
)
async def list_rules_endpoint(line_id: str) -> dict:
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
    summary="Rule summary: counts by severity + enabled/disabled",
)
async def rules_summary_endpoint(line_id: str) -> dict:
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
    summary="List alert profiles for all business lines that have one",
)
async def list_profiles_endpoint() -> dict:
    line_ids = list_profiles()
    return {
        "count": len(line_ids),
        "lines": [
            {
                "line_id": lid,
                "rule_count": len(load_profile(lid).rules),
            }
            for lid in line_ids
        ],
    }


# ─────────────────────────────────────────────────────────────────────────
# Check
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/check",
    response_model=AlertCheckResult,
    summary="Run a check; returns triggered alerts (also persisted unless dry_run=true)",
)
async def check_endpoint(req: AlertCheckRequest) -> AlertCheckResult:
    try:
        profile = load_profile(req.line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return check(profile, req)


# ─────────────────────────────────────────────────────────────────────────
# History (read-only)
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/history",
    response_model=AlertHistoryResponse,
    summary="Recent triggered alerts (newest first), paginated",
)
async def history_endpoint(
    line_id: str | None = Query(None, description="filter by business line id"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AlertHistoryResponse:
    items, total = history(line_id, limit, offset)
    return AlertHistoryResponse(
        line_id=line_id,
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


# ─────────────────────────────────────────────────────────────────────────
# Acknowledge / delete
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/acknowledge/{alert_id}",
    response_model=TriggeredAlert,
    summary="Mark an alert as acknowledged",
)
async def acknowledge_endpoint(alert_id: str) -> TriggeredAlert:
    acked = acknowledge(alert_id)
    if acked is None:
        raise HTTPException(status_code=404, detail=f"alert not found: {alert_id}")
    return acked


@router.delete(
    "/{alert_id}",
    summary="Soft-delete (resolve / ignore) an alert",
)
async def delete_endpoint(alert_id: str) -> dict:
    ok = delete(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"alert not found: {alert_id}")
    return {"deleted": alert_id}
