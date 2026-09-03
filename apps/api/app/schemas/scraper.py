"""
apps/api/app/schemas/scraper.py

Pydantic response models for the /api/scrapers/* endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ScraperSummary(BaseModel):
    """One entry in ``GET /api/scrapers``."""

    source_id: str
    name: str
    schedule: str
    enabled: bool
    last_run: Optional[dict[str, Any]] = None
    last_status: Optional[str] = None


class ScraperDetail(ScraperSummary):
    """Detailed view returned by ``GET /api/scrapers/{source_id}``.

    Includes a few sample rows from the most recent run for the UI to
    show without an extra round-trip.
    """

    history: list[dict[str, Any]] = Field(default_factory=list)


class ScraperRunResponse(BaseModel):
    """Response from ``POST /api/scrapers/{source_id}/run``."""

    source_id: str
    name: str
    status: str
    rows: int
    used_fallback: bool = False
    error: Optional[str] = None
    upload_id: Optional[str] = None
    fetched_at: str
    elapsed_ms: int = 0


class ScraperRunAllResponse(BaseModel):
    """Response from ``POST /api/scrapers/run-all``."""

    started_at: datetime
    finished_at: datetime
    results: list[ScraperRunResponse]
