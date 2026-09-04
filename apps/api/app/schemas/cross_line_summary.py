"""
apps/api/app/schemas/cross_line_summary.py
==========================================

Pydantic response models for the cross-line summary MVP (G task, 2026-09-04).

Two endpoints are exposed:
  * ``GET /api/finance/summary?lines=<csv>`` — fin_bp_global / fin_bp /
    line_owner / admin / auditor / viewer
  * ``GET /api/hr/summary?lines=<csv>``      — hr_bp_global / hr_bp /
    line_owner / admin / auditor / viewer

Both endpoints accept a comma-separated ``lines`` query parameter:

  * empty / ``*`` / ``all`` → every registered business line (9 today)
  * ``residential,retail``  → only those two
  * unknown line ids        → 400 with the unknown list
  * inaccessible line ids   → 403 with the forbidden list
    (line-scoped users are silently downgraded to "own line only"; see the
     router comment for the rationale)

The payload reuses the dashboard MVP's deterministic mock values for KPI
numbers; the only new structure is the ``totals`` cross-line rollup and
the ``scope`` flag that tells the UI which user class made the call.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Reuse the KPI item shape from the dashboard MVP. Both APIs return the
# same per-KPI structure (line_id / kpi_id / title / value / unit / trend),
# so we don't duplicate the Pydantic model — the dashboard router and the
# cross-line summary router share ``DashboardKpiItem``.
from .dashboard import DashboardKpiItem


# Reuse name so callers (and the OpenAPI schema) see the G endpoint shape
# as ``CrossLineSummaryKpi`` rather than the dashboard's name. The wire
# format is identical.
CrossLineSummaryKpi = DashboardKpiItem


class CrossLineSummaryLine(BaseModel):
    """One row per business line in the summary's ``lines`` block."""

    line_id: str
    line_name: str
    kpi_count: int = Field(..., description="number of KPIs in this view for this line")
    domain: Literal["finance", "hr"] = Field(
        ..., description="the data domain the endpoint targets"
    )


class CrossLineSummaryResponse(BaseModel):
    """Top-level cross-line summary payload.

    ``view``     — which perspective the data was filtered for ("fin" / "hr")
    ``scope``    — "global" for ``*_global`` callers, "business_line" otherwise
                   (a UI hint; not a security boundary)
    ``lines``    — one row per business line the response covers
    ``totals``   — cross-line rollup: same ``kpi_id`` across lines is summed
                   (rate-like KPIs are excluded from rollup; see router)
    ``kpis``     — flat list of per-line KPIs (same shape as the dashboard MVP)
    ``generated_at`` — ISO-8601 timestamp of the response
    """

    view: Literal["fin", "hr"] = Field(..., description="'fin' | 'hr'")
    scope: Literal["global", "business_line"] = Field(
        ...,
        description="'global' for *_global callers, 'business_line' for line-scoped",
    )
    lines: list[CrossLineSummaryLine] = Field(default_factory=list)
    totals: dict[str, Optional[float]] = Field(
        default_factory=dict,
        description=(
            "Cross-line rollup keyed by kpi_id. Summable KPIs (e.g. revenue) "
            "are summed; rate-like KPIs (e.g. margin) are null. See the "
            "router's _is_summable_kpi() helper for the heuristic."
        ),
    )
    kpis: list[CrossLineSummaryKpi] = Field(default_factory=list)
    generated_at: str = Field(..., description="ISO-8601 UTC timestamp")


__all__ = [
    "CrossLineSummaryKpi",
    "CrossLineSummaryLine",
    "CrossLineSummaryResponse",
]
