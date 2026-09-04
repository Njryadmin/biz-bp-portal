"""
apps/api/app/schemas/dashboard.py
================================

Pydantic response models for the per-perspective dashboard MVP
(commit E, 2026-09-04).

Three views are exposed:
  * ``fin``     — fin_bp / fin_bp_global / line_owner / admin / auditor
  * ``hr``      — hr_bp / hr_bp_global / line_owner / admin / auditor
  * ``shared``  — anyone authenticated (shared_kpis are public-per-line)

Each view returns a ``DashboardResponse`` with a flat list of KPI cards
plus a per-line summary. The KPI values are MOCK (deterministic hash
over ``line_id + kpi_id``); the real mart-table wiring is out of scope
for this MVP.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DashboardKpiItem(BaseModel):
    """Single KPI card payload.

    Mirrors ``packages/types/src/index.ts: DashboardKpiItem`` (the wire
    contract is duplicated in TypeScript so the frontend doesn't need a
    code-generation step).
    """

    line_id: str = Field(..., description="business line id")
    kpi_id: str = Field(..., description="KPI id from manifest.yaml kpis block")
    title: str = Field(..., description="human-readable title")
    value: float = Field(..., description="current value (mocked)")
    unit: str = Field(default="", description="display unit, e.g. '元' / '%' / '人'")
    trend: str = Field(default="", description="e.g. '+5%' / '-3%' / '—'")
    source: Optional[str] = Field(
        default=None, description="mart table hint, optional"
    )
    formula: Optional[str] = Field(
        default=None, description="derived-metric expression, optional"
    )


class DashboardLine(BaseModel):
    """One row per business line in the dashboard summary."""

    line_id: str
    line_name: str
    kpi_count: int = Field(..., description="number of KPIs in this view for this line")


class DashboardResponse(BaseModel):
    """Top-level dashboard payload."""

    view: str = Field(..., description="'fin' | 'hr' | 'shared'")
    kpis: list[DashboardKpiItem] = Field(default_factory=list)
    lines: list[DashboardLine] = Field(default_factory=list)


__all__ = [
    "DashboardKpiItem",
    "DashboardLine",
    "DashboardResponse",
]
