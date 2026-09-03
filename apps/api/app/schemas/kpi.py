"""
apps/api/app/schemas/kpi.py

Pydantic v2 models for the /kpi endpoint. Business lines return KPI series
that conform to KpiResponse.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class KpiItem(BaseModel):
    indicator_id: str
    value: Optional[float] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    unit: Optional[str] = None


class KpiResponse(BaseModel):
    line_id: str
    items: list[KpiItem] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
