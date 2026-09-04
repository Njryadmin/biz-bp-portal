"""
apps/api/app/routers/cross_line_summary.py
==========================================

Cross-line KPI summary MVP (G task, 2026-09-04).

Two read-only endpoints, mounted at the application root (not under any
business line prefix):

  * ``GET /api/finance/summary?lines=<csv>`` — fin_bp_global cross-line
                                                   FIN view rollup
  * ``GET /api/hr/summary?lines=<csv>``      — hr_bp_global cross-line
                                                   HR view rollup

Goal
----
The per-perspective dashboard MVP (E, 2026-09-04) lets a line-scoped
``fin_bp`` / ``hr_bp`` see the KPIs for *their own line*, and a
``fin_bp_global`` / ``hr_bp_global`` see KPIs for *every* line. That is
fine for browsing, but a集团级 FINBP / HRBP (集团财务负责人 / 集团人力负责人)
needs a **single rolled-up view of the total book** — one number per KPI
that represents the sum across every business line.

The dashboard MVP cannot do this because it has no rollup. The G task
adds two endpoints that produce a ``totals`` block where every KPI
appears exactly once, with the cross-line sum. Line-scoped users
(``fin_bp`` / ``hr_bp`` / ``line_owner``) can also call these endpoints —
they will get the totals for *their own* line only (the cross-line param
is silently ignored for them; see the ``_resolve_accessible_lines`` helper).

RBAC matrix (mirrors the dashboard E semantics; the 2 endpoints are
the *aggregate* form of the dashboard's per-line view):

  * ``fin_bp_global``    — finance summary: full cross-line rollup
  * ``fin_bp``           — finance summary: own line only, ``lines`` param
                            is ignored (downgraded silently)
  * ``hr_bp_global``     — HR summary: full cross-line rollup
  * ``hr_bp``            — HR summary: own line only, ``lines`` ignored
  * ``line_owner``       — both summaries (any line they own)
  * ``admin`` / ``auditor`` / ``viewer`` — both summaries, full cross-line
  * opposite domain      — 403 (e.g. ``hr_bp_global`` calling
                            ``/api/finance/summary``)

Wire shape
----------
::

    {
      "view": "fin" | "hr",
      "scope": "global" | "business_line",
      "lines": [
        {"line_id": "residential", "line_name": "住宅", "kpi_count": 5,
         "domain": "finance"}
      ],
      "totals": {
        "monthly_revenue": 1234567.89,
        "gross_margin": null      # rate-like KPIs are not summed
      },
      "kpis": [
        {"line_id": "residential", "kpi_id": "monthly_revenue",
         "title": "月度营收", "value": 12345.67, "unit": "元",
         "trend": "+5%"}
      ],
      "generated_at": "2026-09-04T..."
    }

Data source
-----------
Identical to the dashboard MVP (E):
  * manifest.yaml ``kpis.{fin_view|hr_view|shared_view}`` per line
  * ``_mock_value`` / ``_mock_trend`` deterministic hash over
    ``line_id + kpi_id``
  * rollup is computed in-memory — no mart queries

Caching
-------
The manifest read uses ``_read_manifest_raw`` directly (no LRU) so an
admin who just PATCH'd a manifest will see the new KPIs on the next
call. ``load_registry()`` is used only for the line-id set (cheap, but
cached by the core).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError

from ..core.auth_v2 import CurrentUserV2, get_current_user_v2
from ..core.logging import get_logger
from ..core.rbac_v2 import DataDomain, Role
from ..core.registry import get_project_root, load_registry
from ..schemas.cross_line_summary import (
    CrossLineSummaryKpi,
    CrossLineSummaryLine,
    CrossLineSummaryResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["cross-line-summary"])


# ---------------------------------------------------------------------------
# Constants — keep in sync with dashboard.py and manifest v2 schema
# ---------------------------------------------------------------------------

# KPI view keys used per endpoint. The finance summary uses fin + shared,
# the HR summary uses hr + shared. Mirrors the dashboard router exactly.
_FIN_VIEW_KEYS: tuple[str, ...] = ("fin_view", "shared_view")
_HR_VIEW_KEYS: tuple[str, ...] = ("hr_view", "shared_view")


# Heuristic: KPI ids / unit suffixes that look like rates (margins,
# variance ratios, utilization percentages) are NOT summed cross-line.
# Summing margins is a category error — a 30% margin on $10M and a 40%
# margin on $5M does not give a 70% margin on $15M; it gives 33.3%.
#
# We use unit / id substrings rather than a manifest field because the
# existing manifests (project-management + _template) only declare
# ``id / title / source / formula`` and not an aggregation hint. A P2
# follow-up may add a ``format: rate|sum`` field; for now this is the
# least-bad heuristic.
_RATE_LIKE_KEYWORDS: tuple[str, ...] = (
    "margin",
    "rate",
    "ratio",
    "variance",
    "utilization",
    "utilisation",
    "utiliz",
    "毛利率",
    "率",
    "占比",
    "偏差",
    "利用率",
    "满意度",
    "满意度",
)


# ---------------------------------------------------------------------------
# Manifest reading — same dance as the dashboard router (deliberately
# duplicated rather than imported so the two routers can evolve
# independently)
# ---------------------------------------------------------------------------


def _read_manifest_raw(line_id: str) -> dict[str, Any] | None:
    """Read ``business_lines/<line_id>/manifest.yaml`` as a raw dict.

    Returns None if the manifest is missing or unparseable. A bad manifest
    must NEVER break the whole summary response — the caller will skip
    that line and continue.
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
            "cross_line_summary: manifest %s parse error: %s", path, exc
        )
        return None
    if not isinstance(data, dict):
        return None
    return data


def _line_display_name(line_id: str) -> str:
    raw = _read_manifest_raw(line_id)
    if raw is None:
        return line_id
    return str(raw.get("name") or line_id)


# ---------------------------------------------------------------------------
# Mock value / trend — SAME scheme as dashboard.py. We copy the helpers
# rather than import them so a future refactor of either router doesn't
# silently change the other's response shape. (They are 1-line copies.)
# ---------------------------------------------------------------------------


def _mock_value(line_id: str, kpi_id: str) -> float:
    h = hashlib.sha256(f"{line_id}:{kpi_id}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 1000.0


def _mock_trend(line_id: str, kpi_id: str) -> str:
    h = hashlib.sha256(f"trend:{line_id}:{kpi_id}".encode("utf-8")).hexdigest()
    pct = int(h[:3], 16) % 30 - 15
    if pct == 0:
        return "—"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct}%"


# ---------------------------------------------------------------------------
# Helpers exposed for tests (prefixed _ so they're "private" but the test
# file imports them to assert behaviour without HTTP roundtrip)
# ---------------------------------------------------------------------------


def _parse_lines_query(
    lines_param: str | None, all_line_ids: list[str]
) -> tuple[list[str], list[str]]:
    """Resolve the ``lines`` query param.

    Returns ``(allowed, unknown)``:
      * ``allowed`` — known line ids in the order the user requested
                      (deduped, order-preserving); empty list when the
                      user requested only unknown ids
      * ``unknown`` — line ids the user asked for that don't exist in
                      the registry; empty list when the request is valid

    Empty / ``*`` / ``all`` (case-insensitive) → all lines, no unknowns.
    """
    if not lines_param or lines_param.strip().lower() in ("", "*", "all"):
        return list(all_line_ids), []
    requested = [s.strip() for s in lines_param.split(",") if s.strip()]
    # Dedupe preserving order (dict.fromkeys does this idiomatically).
    requested = list(dict.fromkeys(requested))
    known = set(all_line_ids)
    unknown = [lid for lid in requested if lid not in known]
    allowed = [lid for lid in requested if lid in known]
    return allowed, unknown


def _check_cross_line_access(
    user: CurrentUserV2,
    line_id: str,
    domain: DataDomain,
    require_global: bool = False,
) -> bool:
    """Per-line access predicate used by other routers that wrap the
    cross-line summary.

    Returns True iff the user can VIEW ``domain`` on ``line_id``.

    * ``require_global=True``  — only ``*_global`` / line_owner /
      admin / auditor / viewer may pass. Line-scoped roles (fin_bp /
      hr_bp) are rejected even on their own line.
    * ``require_global=False`` — line-scoped roles pass on their own
      line; global roles always pass.

    Note: the cross-line summary router itself uses
    ``_resolve_accessible_lines`` for the silent-downgrade policy
    (line-scoped users get their own line regardless of the param).
    This helper is provided for callers that need a strict per-line
    check (e.g. a future drill-down endpoint).
    """
    if user.has_role(Role.ADMIN, Role.AUDITOR, Role.VIEWER):
        return True
    if require_global:
        # Only the matching global role for the requested domain.
        return user.has_role(
            Role.FIN_BP_GLOBAL
            if domain == DataDomain.FINANCE
            else Role.HR_BP_GLOBAL
        )
    # Line-scoped: user must have explicit view access on the line.
    return user.can_access_domain(line_id, domain, write=False)


def _is_summable_kpi(kpi_id: str, unit: str, title: str) -> bool:
    """True if the KPI is a "sum" type (revenue, headcount) rather than
    a "rate" type (margin, variance).

    Rate-like KPIs are excluded from the ``totals`` cross-line rollup
    because summing a rate across lines is mathematically wrong (see the
    module docstring for the worked example).
    """
    blob = f"{kpi_id} {unit} {title}".lower()
    for kw in _RATE_LIKE_KEYWORDS:
        if kw.lower() in blob:
            return False
    return True


# ---------------------------------------------------------------------------
# Access decision
# ---------------------------------------------------------------------------


def _user_has_domain_anywhere(
    user: CurrentUserV2, all_line_ids: list[str], domain: DataDomain
) -> bool:
    """True iff the user can VIEW ``domain`` on at least one line.

    Used to short-circuit the endpoints with 403 when the user has zero
    lines granting the requested domain. (Mirrors dashboard.py:261-281.)
    """
    for lid in all_line_ids:
        if user.can_access_domain(lid, domain, write=False):
            return True
    return False


def _resolve_accessible_lines(
    user: CurrentUserV2,
    requested: list[str],
    all_line_ids: list[str],
    domain: DataDomain,
) -> tuple[list[str], bool]:
    """Apply the cross-line RBAC policy and return ``(allowed, is_global)``.

    Policy:
      * Global-scope users (admin / auditor / viewer / ``*_global``) get
        everything they asked for, provided it's a known line id. The
        boolean returned is ``True`` — the response ``scope`` will be
        ``"global"``.
      * Line-scoped users (line_owner / fin_bp / hr_bp) get their own
        line only; the cross-line param is silently downgraded (UI sees
        a 200 with a single-line result, not a 403). ``is_global=False``.
      * If the user has zero view access to ``domain``, the function
        returns an empty list and ``is_global=False`` — the caller is
        expected to 403 on the empty result.

    The boolean is used to populate the response's ``scope`` field; it
    reflects the *scope of the data the user actually sees*, not the
    scope of the user's bindings.
    """
    is_global_actor = (
        any(b.role in (Role.ADMIN, Role.AUDITOR, Role.VIEWER) for b in user.bindings)
        or user.has_role(Role.FIN_BP_GLOBAL)
        or user.has_role(Role.HR_BP_GLOBAL)
    )

    if is_global_actor:
        # Global-scope users get what they asked for (already filtered to
        # known ids by the caller). ``is_global_actor`` here is "the user
        # is permitted to see totals across multiple lines" — the actual
        # response ``scope`` flag.
        return requested, is_global_actor

    # Line-scoped: silently downgrade to the user's own line(s).
    own_lines = user.filter_accessible_lines(all_line_ids)
    if not own_lines:
        return [], False
    # Filter requested list to only lines the user actually owns.
    allowed = [lid for lid in requested if lid in own_lines]
    # If the user supplied no lines / unknown lines / lines they don't
    # own, fall back to all of their own lines so they always get *some*
    # data instead of a confusing 200-with-empty.
    if not allowed:
        allowed = own_lines
    return allowed, False


# ---------------------------------------------------------------------------
# Per-line KPI gather
# ---------------------------------------------------------------------------


def _build_kpi_item(line_id: str, raw_kpi: dict[str, Any]) -> CrossLineSummaryKpi | None:
    kpi_id = raw_kpi.get("id")
    title = raw_kpi.get("title")
    if not isinstance(kpi_id, str) or not kpi_id:
        return None
    if not isinstance(title, str) or not title:
        return None
    return CrossLineSummaryKpi(
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
    line_id: str, view_keys: Iterable[str]
) -> list[CrossLineSummaryKpi]:
    raw = _read_manifest_raw(line_id)
    if raw is None:
        return []
    kpis_block = raw.get("kpis") or {}
    if not isinstance(kpis_block, dict):
        return []
    out: list[CrossLineSummaryKpi] = []
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


def _gather_kpis_for_lines(
    line_ids: list[str], view_keys: Iterable[str]
) -> list[CrossLineSummaryKpi]:
    out: list[CrossLineSummaryKpi] = []
    for lid in line_ids:
        try:
            out.extend(_gather_kpis(lid, view_keys))
        except ValidationError as exc:
            logger.warning(
                "cross_line_summary: skipping line %s due to KPI "
                "validation error: %s",
                lid,
                exc,
            )
    return out


def _compute_totals(
    kpis: list[CrossLineSummaryKpi],
) -> dict[str, float | None]:
    """Roll up the same ``kpi_id`` across lines.

    Summable KPIs (e.g. revenue, headcount) are summed. Rate-like KPIs
    (margin / variance / utilization) are left as ``None`` — the
    heuristic is in ``_is_summable_kpi``.

    The first time we see a ``kpi_id`` we store its unit so subsequent
    occurrences can be summed even if a manifest forgot to set the
    unit on the second declaration (rare; defensive).
    """
    totals: dict[str, float | None] = {}
    for item in kpis:
        existing = totals.get(item.kpi_id)
        if existing is None and item.kpi_id in totals:
            # Already marked as "rate-like, not summable" — skip
            continue
        if not _is_summable_kpi(item.kpi_id, item.unit, item.title):
            totals[item.kpi_id] = None
            continue
        if item.kpi_id not in totals:
            totals[item.kpi_id] = item.value
        else:
            totals[item.kpi_id] = (totals[item.kpi_id] or 0.0) + item.value
    return totals


# ---------------------------------------------------------------------------
# Shared endpoint body — both /finance and /hr call into here
# ---------------------------------------------------------------------------


def _build_response(
    user: CurrentUserV2,
    all_line_ids: list[str],
    requested_lines_param: str | None,
    domain: DataDomain,
    view_label: str,
    view_keys: tuple[str, ...],
) -> CrossLineSummaryResponse:
    # 1. Domain gate: the user must have at least one line granting this
    #    domain. Otherwise 403 (consistent with the dashboard MVP).
    if not _user_has_domain_anywhere(user, all_line_ids, domain):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"no {domain.value.upper()} view access on any accessible "
                f"line; user roles={user.roles}"
            ),
        )

    # 2. Parse ``lines`` query param into (allowed, unknown).
    allowed, unknown = _parse_lines_query(requested_lines_param, all_line_ids)
    if unknown:
        # Unknown line ids are a hard 400 (we cannot guess what the user
        # meant). Listing them in the detail lets the UI highlight them.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "unknown_line_ids",
                "unknown": unknown,
            },
        )

    # 3. Resolve accessible lines (downgrades silently for line-scoped
    #    users; the result is always the lines the response will cover).
    if not allowed:
        # User supplied an empty / "*" / "all" param → all known lines.
        # Resolve against the user's scope (global vs line-scoped).
        resolved, is_global = _resolve_accessible_lines(
            user, list(all_line_ids), all_line_ids, domain
        )
    else:
        resolved, is_global = _resolve_accessible_lines(
            user, allowed, all_line_ids, domain
        )

    if not resolved:
        # Line-scoped user with no lines at all (shouldn't happen given
        # the domain gate above, but defensive).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"no {domain.value.upper()} view access on the requested "
                f"lines; user roles={user.roles}"
            ),
        )

    # 4. Gather KPIs across the resolved lines.
    kpis = _gather_kpis_for_lines(resolved, view_keys)

    # 5. Per-line summary + totals rollup.
    counts: dict[str, int] = {lid: 0 for lid in resolved}
    for item in kpis:
        counts[item.line_id] = counts.get(item.line_id, 0) + 1
    lines_block = [
        CrossLineSummaryLine(
            line_id=lid,
            line_name=_line_display_name(lid),
            kpi_count=counts.get(lid, 0),
            domain=domain.value,  # "finance" | "hr"
        )
        for lid in resolved
    ]
    totals = _compute_totals(kpis)

    return CrossLineSummaryResponse(
        view=view_label,  # type: ignore[arg-type]
        scope="global" if is_global else "business_line",
        lines=lines_block,
        totals=totals,
        kpis=kpis,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/finance/summary",
    response_model=CrossLineSummaryResponse,
    summary=(
        "FIN 视角跨业务线汇总 (RBAC: FINANCE view) — fin_bp_global 跨线, "
        "fin_bp / line_owner 仅本线, hr_bp* 403"
    ),
)
async def finance_summary(
    request: Request,
    lines: str | None = Query(
        default=None,
        description=(
            "Comma-separated business line ids. Empty / '*' / 'all' → all 9 "
            "lines. Unknown ids → 400. Line-scoped users get their own line "
            "regardless of this param."
        ),
    ),
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> CrossLineSummaryResponse:
    """GET /api/finance/summary?lines=residential,retail

    fin_bp_global  → cross-line rollup over the requested lines
    fin_bp / line_owner → 200 with their own line only (param ignored)
    admin / auditor / viewer → full cross-line rollup
    hr_bp / hr_bp_global → 403 (no FINANCE view)
    """
    all_line_ids = [e.line.id for e in load_registry()]
    return _build_response(
        user=user,
        all_line_ids=all_line_ids,
        requested_lines_param=lines,
        domain=DataDomain.FINANCE,
        view_label="fin",
        view_keys=_FIN_VIEW_KEYS,
    )


@router.get(
    "/hr/summary",
    response_model=CrossLineSummaryResponse,
    summary=(
        "HR 视角跨业务线汇总 (RBAC: HR view) — hr_bp_global 跨线, "
        "hr_bp / line_owner 仅本线, fin_bp* 403"
    ),
)
async def hr_summary(
    request: Request,
    lines: str | None = Query(
        default=None,
        description=(
            "Comma-separated business line ids. Empty / '*' / 'all' → all 9 "
            "lines. Unknown ids → 400. Line-scoped users get their own line "
            "regardless of this param."
        ),
    ),
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> CrossLineSummaryResponse:
    """GET /api/hr/summary?lines=residential,retail

    hr_bp_global  → cross-line rollup over the requested lines
    hr_bp / line_owner → 200 with their own line only (param ignored)
    admin / auditor / viewer → full cross-line rollup
    fin_bp / fin_bp_global → 403 (no HR view)
    """
    all_line_ids = [e.line.id for e in load_registry()]
    return _build_response(
        user=user,
        all_line_ids=all_line_ids,
        requested_lines_param=lines,
        domain=DataDomain.HR,
        view_label="hr",
        view_keys=_HR_VIEW_KEYS,
    )


__all__ = [
    "router",
    "finance_summary",
    "hr_summary",
    # Helpers exported for unit tests in test_cross_line_summary.py
    "_parse_lines_query",
    "_check_cross_line_access",
    "_is_summable_kpi",
    "_resolve_accessible_lines",
    "_compute_totals",
]
