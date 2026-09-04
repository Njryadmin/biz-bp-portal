"""
apps/api/app/routers/dashboard.py
================================

Per-perspective dashboard MVP (commit E, 2026-09-04).

Mounted at ``/api/dashboard`` by ``app.main``. Three read-only endpoints,
one per viewpoint:

* ``GET /api/dashboard/fin``     — FIN 视角 KPI  (checks FINANCE domain)
* ``GET /api/dashboard/hr``      — HR 视角 KPI   (checks HR domain)
* ``GET /api/dashboard/shared``  — 共享视角 KPI  (no domain check)

All three use ``get_current_user_v2`` so the optional ``X-Active-View``
header propagates ``active_view`` through to audit / logging. The
``X-Active-View`` does NOT change the data access decision here — the
view the client requested IS the data shape it gets. It's only a hint
to other consumers (Copilot, audit) about user intent.

Data source
-----------
For each business line in the user's ``accessible_lines``, we read
``manifest.yaml:kpis.{fin_view, hr_view, shared_view}`` and emit one
``DashboardKpiItem`` per entry. The ``value`` / ``trend`` are MOCK
(deterministic hash over ``line_id + kpi_id``); wiring real mart data
is a follow-up (P2).

RBAC semantics
--------------
* ``/fin``     — the user must have at least one accessible line where
                 they can VIEW the FINANCE domain. Otherwise 403.
* ``/hr``      — same, but for the HR domain.
* ``/shared``  — no domain check (shared KPIs are designed to be visible
                 to anyone who can see the line).

Line-scoped roles (``fin_bp`` / ``hr_bp``) only see their own line
because ``filter_accessible_lines`` filters to ``accessible_lines`` for
business_line-scope roles. Global roles (``fin_bp_global`` /
``hr_bp_global`` / ``admin`` / ``auditor`` / ``viewer``) see every line
they're granted. This matches the cross-line router pattern
(alerts / forecast / sensitivity / copilot).
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from ..core.auth_v2 import CurrentUserV2, get_current_user_v2
from ..core.logging import get_logger
from ..core.rbac_v2 import DataDomain
from ..core.registry import get_project_root, load_registry
from ..schemas.dashboard import (
    DashboardKpiItem,
    DashboardLine,
    DashboardResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 3 KPI view keys — kept in sync with manifest v2 schema and
# apps/web/lib/business-lines.ts:V2_KPI_VIEWS. Hard-coded here rather
# than imported from a constant module to keep the router self-contained.
_KPI_VIEW_KEYS: tuple[str, ...] = ("fin_view", "hr_view", "shared_view")


# ---------------------------------------------------------------------------
# Manifest reading (raw — admin router does the same dance)
# ---------------------------------------------------------------------------


def _read_manifest_raw(line_id: str) -> dict[str, Any] | None:
    """Read ``business_lines/<line_id>/manifest.yaml`` as a raw dict.

    Returns None if the manifest is missing or unparseable. We never
    raise here — one bad manifest must not break the whole dashboard
    response.
    """
    root = get_project_root()
    path = root / "business_lines" / line_id / "manifest.yaml"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        logger.warning(
            "dashboard: manifest %s parse error: %s", path, exc
        )
        return None
    if not isinstance(data, dict):
        return None
    return data


def _line_display_name(line_id: str) -> str:
    """Best-effort display name from the manifest. Falls back to id."""
    raw = _read_manifest_raw(line_id)
    if raw is None:
        return line_id
    return str(raw.get("name") or line_id)


# ---------------------------------------------------------------------------
# Mock value / trend (deterministic hash — no randomness in tests)
# ---------------------------------------------------------------------------


def _mock_value(line_id: str, kpi_id: str) -> float:
    """Deterministic mock value in [0, 1_000_000) (3 decimals).

    Stable: same ``(line_id, kpi_id)`` always yields the same number,
    so the frontend can compare snapshots across renders without
    layout thrash.
    """
    h = hashlib.sha256(f"{line_id}:{kpi_id}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 1000.0


def _mock_trend(line_id: str, kpi_id: str) -> str:
    """Deterministic mock trend like ``"+5%"`` / ``"-3%"`` / ``"—"``.

    Range: -15% .. +14%. Two consecutive renders of the same KPI return
    the same trend.
    """
    h = hashlib.sha256(f"trend:{line_id}:{kpi_id}".encode("utf-8")).hexdigest()
    pct = int(h[:3], 16) % 30 - 15
    if pct == 0:
        return "—"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct}%"


# ---------------------------------------------------------------------------
# Per-view builder
# ---------------------------------------------------------------------------


def _build_kpi_item(
    line_id: str,
    raw_kpi: dict[str, Any],
) -> DashboardKpiItem | None:
    """Validate one raw KPI entry from the manifest into a dashboard item.

    Returns None if ``id`` or ``title`` is missing (the admin editor
    enforces this, but a hand-edited manifest might not).
    """
    kpi_id = raw_kpi.get("id")
    title = raw_kpi.get("title")
    if not isinstance(kpi_id, str) or not kpi_id:
        return None
    if not isinstance(title, str) or not title:
        return None
    return DashboardKpiItem(
        line_id=line_id,
        kpi_id=kpi_id,
        title=title,
        value=_mock_value(line_id, kpi_id),
        unit=str(raw_kpi.get("unit", "") or ""),
        trend=_mock_trend(line_id, kpi_id),
        source=raw_kpi.get("source"),
        formula=raw_kpi.get("formula"),
    )


def _gather_kpis(
    line_id: str,
    view_keys: Iterable[str],
) -> list[DashboardKpiItem]:
    """Read ``manifest.yaml:kpis`` and emit items for the requested views.

    Unknown keys in the manifest are silently skipped; the union of the
    requested views is returned. Missing ``kpis`` block → empty list
    (e.g. 8 of 9 business lines have empty KPI sets today).
    """
    raw = _read_manifest_raw(line_id)
    if raw is None:
        return []
    kpis_block = raw.get("kpis") or {}
    if not isinstance(kpis_block, dict):
        return []
    out: list[DashboardKpiItem] = []
    for key in view_keys:
        bucket = kpis_block.get(key) or []
        if not isinstance(bucket, list):
            continue
        for raw_kpi in bucket:
            if not isinstance(raw_kpi, dict):
                continue
            item = _build_kpi_item(line_id, raw_kpi)
            if item is not None:
                out.append(item)
    return out


def _line_summaries(
    line_ids: list[str],
    kpi_items: list[DashboardKpiItem],
) -> list[DashboardLine]:
    """Group KPI items by line and produce the ``lines`` summary."""
    counts: dict[str, int] = {lid: 0 for lid in line_ids}
    for item in kpi_items:
        counts[item.line_id] = counts.get(item.line_id, 0) + 1
    return [
        DashboardLine(
            line_id=lid,
            line_name=_line_display_name(lid),
            kpi_count=counts.get(lid, 0),
        )
        for lid in line_ids
    ]


# ---------------------------------------------------------------------------
# Domain-access check helper (per-line iteration)
# ---------------------------------------------------------------------------


def _any_line_has_domain(
    user: CurrentUserV2,
    line_ids: list[str],
    domain: DataDomain,
) -> bool:
    """True iff the user can VIEW ``domain`` on at least one accessible line.

    Used to short-circuit the /fin and /hr endpoints with a 403 when the
    user has *zero* lines granting the requested domain. Per-line
    filtering is still applied by ``filter_accessible_lines`` —
    this is just the gate check.
    """
    for lid in line_ids:
        if user.can_access_domain(lid, domain, write=False):
            return True
    return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/fin",
    response_model=DashboardResponse,
    summary="FIN 视角 KPI — fin_view + shared_view, per accessible line (RBAC: FINANCE view)",
)
async def fin_dashboard(
    request: Request,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> DashboardResponse:
    """Return FIN-view KPIs for every business line the user can see.

    403 if the user has no FINANCE view access on any accessible line
    (e.g. a pure ``hr_bp`` user requesting ``/fin``).
    """
    all_line_ids = [e.line.id for e in load_registry()]
    accessible = user.filter_accessible_lines(all_line_ids)
    if not accessible:
        # No lines at all — 403 (consistent with the "no view access" path).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no accessible business lines",
        )
    if not _any_line_has_domain(user, accessible, DataDomain.FINANCE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"no FINANCE view access on any accessible line; "
                f"user roles={user.roles}"
            ),
        )
    kpis = _gather_kpis_for_lines(accessible, ("fin_view", "shared_view"))
    return DashboardResponse(
        view="fin",
        kpis=kpis,
        lines=_line_summaries(accessible, kpis),
    )


@router.get(
    "/hr",
    response_model=DashboardResponse,
    summary="HR 视角 KPI — hr_view + shared_view, per accessible line (RBAC: HR view)",
)
async def hr_dashboard(
    request: Request,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> DashboardResponse:
    """Return HR-view KPIs for every business line the user can see.

    403 if the user has no HR view access on any accessible line
    (e.g. a pure ``fin_bp`` user requesting ``/hr``).
    """
    all_line_ids = [e.line.id for e in load_registry()]
    accessible = user.filter_accessible_lines(all_line_ids)
    if not accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no accessible business lines",
        )
    if not _any_line_has_domain(user, accessible, DataDomain.HR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"no HR view access on any accessible line; "
                f"user roles={user.roles}"
            ),
        )
    kpis = _gather_kpis_for_lines(accessible, ("hr_view", "shared_view"))
    return DashboardResponse(
        view="hr",
        kpis=kpis,
        lines=_line_summaries(accessible, kpis),
    )


@router.get(
    "/shared",
    response_model=DashboardResponse,
    summary="共享视角 KPI — shared_view only, per accessible line (no domain check)",
)
async def shared_dashboard(
    request: Request,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> DashboardResponse:
    """Return shared-view KPIs for every business line the user can see.

    No domain check — shared KPIs are designed to be visible to anyone
    who can see the line. 401/403 are not expected (the dep already
    authenticated the user; the empty-accessible-lines case is the
    only 403 path).
    """
    all_line_ids = [e.line.id for e in load_registry()]
    accessible = user.filter_accessible_lines(all_line_ids)
    if not accessible:
        # Empty list is still a valid response (no lines → no KPIs).
        # 200 with empty arrays — keeps the frontend's "no data" branch
        # trivial and avoids a 403 that would block the /shared view
        # for users with no business-line bindings.
        return DashboardResponse(view="shared", kpis=[], lines=[])
    kpis = _gather_kpis_for_lines(accessible, ("shared_view",))
    return DashboardResponse(
        view="shared",
        kpis=kpis,
        lines=_line_summaries(accessible, kpis),
    )


# ---------------------------------------------------------------------------
# Helper: per-line kpi gather, isolated so a single bad manifest doesn't
# break the response (defensive: the manifest editor already validates,
# but a hand-edited file in dev should not 500 the whole page).
# ---------------------------------------------------------------------------


def _gather_kpis_for_lines(
    line_ids: list[str],
    view_keys: Iterable[str],
) -> list[DashboardKpiItem]:
    out: list[DashboardKpiItem] = []
    for lid in line_ids:
        try:
            out.extend(_gather_kpis(lid, view_keys))
        except ValidationError as exc:
            logger.warning(
                "dashboard: skipping line %s due to KPI validation error: %s",
                lid,
                exc,
            )
    return out


__all__ = [
    "router",
    "fin_dashboard",
    "hr_dashboard",
    "shared_dashboard",
]
