"""
apps/api/app/routers/scrapers.py

HTTP router for the web-scraping framework. Mounted at ``/api/scrapers``
by ``app.main``.

Endpoints:

* ``GET    /api/scrapers``                  — list all registered scrapers
                                              (auth required)
* ``GET    /api/scrapers/{source_id}``      — detail (incl. last 10 runs)
                                              (auth required)
* ``POST   /api/scrapers/{source_id}/run``  — run one scraper now
                                              (admin only)
* ``POST   /api/scrapers/run-all``          — run every enabled scraper
                                              (admin only)
* ``GET    /api/scrapers/history/{source_id}`` — last 10 historical runs
                                              (auth required)

Failures from inside a scraper (network, parsing, validation) are
handled by the framework's fallback chain; this router should NEVER
return a 5xx because a scraper failed to fetch. The only way a 4xx
is returned is for an unknown ``source_id`` or a bad JSON body.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..core.auth import CurrentUser, get_current_user
from ..core.logging import get_logger
from ..core.rbac import require_admin_dep
from ..schemas.scraper import (
    ScraperDetail,
    ScraperRunAllResponse,
    ScraperRunResponse,
    ScraperSummary,
)
from ..services.scrapers import discover_scrapers, get, run_all, run_one
from ..services.scrapers.persist import last_scraper_run, scraper_history

logger = get_logger(__name__)

router = APIRouter(prefix="/api/scrapers", tags=["scrapers"])


# ─────────────────────────────────────────────────────────────────────────
# Internal: build summary + detail DTOs
# ─────────────────────────────────────────────────────────────────────────


def _to_summary(s: Any, last: dict[str, Any] | None) -> ScraperSummary:
    last_status: str | None = None
    if last and last.get("row_count") is not None:
        last_status = "ok"
    return ScraperSummary(
        source_id=getattr(s, "source_id", ""),
        name=getattr(s, "name", ""),
        schedule=getattr(s, "schedule", ""),
        enabled=getattr(s, "enabled", True),
        last_run=last,
        last_status=last_status,
    )


async def _list_with_history() -> list[ScraperSummary]:
    discover_scrapers()
    from ..services.scrapers.registry import get_all

    out: list[ScraperSummary] = []
    for s in get_all():
        last = await last_scraper_run(getattr(s, "source_id", ""))
        out.append(_to_summary(s, last))
    return out


# ─────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[ScraperSummary],
    summary="List every registered scraper with last-run status (auth required)",
)
async def list_scrapers(
    _user: CurrentUser = Depends(get_current_user),
) -> list[ScraperSummary]:
    return await _list_with_history()


@router.get(
    "/run-all",
    response_model=ScraperRunAllResponse,
    summary="Alias: run every scraper now (GET variant for browser convenience; admin only)",
)
async def run_all_get(
    _user: CurrentUser = Depends(require_admin_dep),
) -> ScraperRunAllResponse:
    return await _run_all_handler()


@router.post(
    "/run-all",
    response_model=ScraperRunAllResponse,
    summary="Run every enabled scraper; persist the result to raw.uploads (admin only)",
)
async def run_all_post(
    _user: CurrentUser = Depends(require_admin_dep),
) -> ScraperRunAllResponse:
    return await _run_all_handler()


async def _run_all_handler() -> ScraperRunAllResponse:
    started = datetime.now(timezone.utc)
    raw_results = await run_all(persist=True)
    finished = datetime.now(timezone.utc)
    results: list[ScraperRunResponse] = []
    for r in raw_results:
        results.append(
            ScraperRunResponse(
                source_id=r.get("source_id", ""),
                name=r.get("name", ""),
                status=r.get("status", "error"),
                rows=r.get("rows", 0),
                used_fallback=r.get("used_fallback", False),
                error=r.get("error"),
                upload_id=r.get("upload_id"),
                fetched_at=r.get("fetched_at", finished.isoformat()),
                elapsed_ms=r.get("elapsed_ms", 0),
            )
        )
    return ScraperRunAllResponse(
        started_at=started,
        finished_at=finished,
        results=results,
    )


@router.get(
    "/{source_id}",
    response_model=ScraperDetail,
    summary="Scraper detail (metadata + last 10 runs) (auth required)",
)
async def get_scraper(
    source_id: str,
    _user: CurrentUser = Depends(get_current_user),
) -> ScraperDetail:
    discover_scrapers()
    s = get(source_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"unknown source_id: {source_id}")
    history = await scraper_history(source_id, limit=10)
    last = history[0] if history else None
    summary = _to_summary(s, last)
    return ScraperDetail(**summary.model_dump(), history=history)


@router.post(
    "/{source_id}/run",
    response_model=ScraperRunResponse,
    summary="Run a single scraper now; persist the result (admin only)",
)
async def run_scraper(
    source_id: str,
    _user: CurrentUser = Depends(require_admin_dep),
) -> ScraperRunResponse:
    raw = await run_one(source_id, persist=True)
    if isinstance(raw, dict) and raw.get("status") == "error" and raw.get("error", "").startswith("unknown"):
        raise HTTPException(status_code=404, detail=raw.get("error", "unknown"))
    return ScraperRunResponse(
        source_id=raw.get("source_id", source_id),
        name=raw.get("name", source_id),
        status=raw.get("status", "ok"),
        rows=raw.get("rows", 0),
        used_fallback=raw.get("used_fallback", False),
        error=raw.get("error"),
        upload_id=raw.get("upload_id"),
        fetched_at=raw.get("fetched_at", datetime.now(timezone.utc).isoformat()),
        elapsed_ms=raw.get("elapsed_ms", 0),
    )


@router.get(
    "/history/{source_id}",
    summary="Last 10 raw.uploads rows for a given scraper source_id (auth required)",
)
async def get_scraper_history(
    source_id: str,
    limit: int = 10,
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    discover_scrapers()
    if get(source_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown source_id: {source_id}")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be in 1..200")
    return await scraper_history(source_id, limit=limit)
