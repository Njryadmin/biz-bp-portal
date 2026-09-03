"""
apps/api/app/services/scrapers/base.py

Abstract ``BaseScraper`` and the runtime :class:`Scraper` protocol that the
registry duck-types against. New scrapers MUST inherit ``BaseScraper``.

The lifecycle of one scraper execution is:

    fetch()    ──▶  parse()   ──▶  validate()  ──▶  to_landing_row()
    raw dicts       raw dicts       raw dicts        raw.uploads row

Subclasses MAY override the default ``to_landing_row`` (which simply
adds ``source`` / ``fetched_at`` metadata) when they need to map the
parsed dict into a custom shape (e.g. wrap an array of rows into a
single payload record).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Protocol, runtime_checkable


# ─────────────────────────────────────────────────────────────────────────
# Result type returned by run_one() / run_all()
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ScraperRunResult:
    """Outcome of a single scraper execution."""

    source_id: str
    name: str
    rows: int = 0
    status: str = "ok"  # "ok" | "degraded" | "error"
    used_fallback: bool = False
    error: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "rows": self.rows,
            "status": self.status,
            "used_fallback": self.used_fallback,
            "error": self.error,
            "fetched_at": self.fetched_at,
            "elapsed_ms": self.elapsed_ms,
        }


# ─────────────────────────────────────────────────────────────────────────
# Protocol (duck-typing contract for tests + non-BaseScraper adapters)
# ─────────────────────────────────────────────────────────────────────────


@runtime_checkable
class Scraper(Protocol):
    """Anything that looks like a scraper.

    The registry duck-types against this. The real implementation is
    :class:`BaseScraper` but a hand-rolled class with the right shape
    also works.
    """

    source_id: str
    name: str
    schedule: str
    enabled: bool

    async def fetch(self) -> list[dict[str, Any]]: ...
    def parse(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def validate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def to_landing_row(self, row: dict[str, Any]) -> dict[str, Any]: ...
    async def run(self) -> ScraperRunResult: ...


# ─────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────


class BaseScraper(ABC):
    """Abstract base every concrete scraper inherits from.

    Subclasses must set the class attributes ``source_id``, ``name`` and
    ``schedule`` and implement ``fetch`` / ``parse`` / ``validate``.
    ``to_landing_row`` has a sensible default.
    """

    # ---- Class-level metadata ------------------------------------------

    source_id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    schedule: ClassVar[str] = "0 2 * * *"
    enabled: ClassVar[bool] = True

    # ---- Lifecycle hooks -----------------------------------------------

    @abstractmethod
    async def fetch(self) -> list[dict[str, Any]]:
        """Pull raw data from the source. Returns a list of dicts.

        Network errors should bubble up; the registry ``run_one`` will
        catch them and trigger the ``fallback()`` hook.
        """
        raise NotImplementedError

    @abstractmethod
    def parse(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize the raw dicts to the canonical schema for this scraper."""
        raise NotImplementedError

    def validate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop or repair invalid rows.

        Default: keep rows whose required fields are non-empty. Subclasses
        may override.
        """
        return [r for r in rows if _row_has_required(r, getattr(self, "required_fields", ()))]

    def fallback(self) -> list[dict[str, Any]]:
        """Return mock data when ``fetch`` raises.

        Subclasses MUST override to return realistic but obviously-fake
        data so the rest of the pipeline can keep running.
        """
        return []

    def to_landing_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a parsed/validated row to the ``raw.uploads`` shape.

        Default: stamp ``source`` (this scraper's ``source_id``) and
        ``fetched_at`` (now in UTC ISO format). Subclasses that need
        to wrap multiple rows into one record should override.
        """
        out = dict(row)
        out.setdefault("source", self.source_id)
        out.setdefault("fetched_at", datetime.now(timezone.utc).isoformat())
        return out

    # ---- Orchestration ------------------------------------------------

    async def run(self) -> ScraperRunResult:
        """Fetch → parse → validate → fallback chain.

        This is the entry point called by the registry's ``run_one`` /
        ``run_all``. Subclasses generally do not need to override it.
        """
        import time

        # Local import keeps top-level dependency graph small for tests.
        from ...core.logging import get_logger as _get_logger

        log = _get_logger(f"scrapers.{self.source_id}")
        t0 = time.perf_counter()
        try:
            raw = await self.fetch()
            parsed = self.parse(raw)
            valid = self.validate(parsed)
            elapsed = int((time.perf_counter() - t0) * 1000)
            log.info("scraper %s fetched %d rows (elapsed=%dms)", self.source_id, len(valid), elapsed)
            return ScraperRunResult(
                source_id=self.source_id,
                name=self.name,
                rows=len(valid),
                status="ok",
                used_fallback=False,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                elapsed_ms=elapsed,
            )
        except Exception as exc:  # noqa: BLE001 — log + fall back, never raise
            elapsed = int((time.perf_counter() - t0) * 1000)
            log.warning("scraper %s fetch failed: %s — using fallback", self.source_id, exc)
            try:
                fallback_rows = self.fallback()
                # ``fallback()`` already returns schema-conformant dicts, so
                # we deliberately skip ``parse()`` (which expects raw HTML
                # wrappers) and go straight to ``validate()``.
                valid = self.validate(fallback_rows)
            except Exception as fb_exc:  # noqa: BLE001
                log.error("scraper %s fallback also failed: %s", self.source_id, fb_exc)
                return ScraperRunResult(
                    source_id=self.source_id,
                    name=self.name,
                    rows=0,
                    status="error",
                    used_fallback=True,
                    error=f"fetch: {exc}; fallback: {fb_exc}",
                    elapsed_ms=elapsed,
                )
            return ScraperRunResult(
                source_id=self.source_id,
                name=self.name,
                rows=len(valid),
                status="degraded",
                used_fallback=True,
                error=str(exc),
                elapsed_ms=elapsed,
            )

    # ---- Persistence helper -------------------------------------------

    async def persist(self, rows: list[dict[str, Any]], run_status: str = "ok") -> str | None:
        """Insert the parsed rows into ``raw.uploads`` as a single JSONB payload.

        Returns the generated ``upload_id`` (or None if no rows).

        ``run_status`` is propagated into ``raw.uploads.run_status`` so
        the dashboard tile can colour-code degraded runs vs clean ones.

        The router wraps this in a try/except and never lets a DB failure
        bubble up to the user; this method does the same.
        """
        if not rows:
            return None
        from .persist import persist_scraper_rows  # local import to avoid cycle
        return await persist_scraper_rows(self.source_id, rows, run_status=run_status)


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _row_has_required(row: dict[str, Any], required: tuple[str, ...]) -> bool:
    """Return True iff every required key is present and non-empty."""
    if not required:
        return True
    for k in required:
        v = row.get(k)
        if v is None:
            return False
        if isinstance(v, str) and not v.strip():
            return False
    return True
