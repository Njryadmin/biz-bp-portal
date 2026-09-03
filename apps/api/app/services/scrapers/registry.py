"""
apps/api/app/services/scrapers/registry.py

In-process registry of all known scrapers. Mirrors the business-line
auto-discovery pattern: a new ``scrapers/<source_id>.py`` file that calls
:func:`register` at import time is picked up the next time
:func:`discover_scrapers` is called (or, in the common path, the next
time the module is imported).

The registry is intentionally a tiny module-level dict — process-local,
thread-safe for the simple ``register``/``get`` operations we need.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import pkgutil
import sys
import threading
from pathlib import Path
from typing import Any

from .base import BaseScraper, Scraper, ScraperRunResult
from ...core.logging import get_logger

logger = get_logger(__name__)

_REGISTRY: dict[str, Scraper] = {}
# Reentrant lock so that module-level ``register(...)`` calls fired by
# ``discover_scrapers`` don't deadlock against the outer ``with _LOCK``.
_LOCK = threading.RLock()
_DISCOVERED = False


# ─────────────────────────────────────────────────────────────────────────
# register / get / get_all
# ─────────────────────────────────────────────────────────────────────────


def register(scraper: Scraper | BaseScraper) -> None:
    """Register a scraper instance by its ``source_id``.

    Idempotent: re-registering the same ``source_id`` overwrites the
    previous entry (useful for tests).
    """
    if not isinstance(scraper, (Scraper, BaseScraper)):
        # Fall back to structural check: must expose ``source_id`` and ``name``.
        if not hasattr(scraper, "source_id") or not hasattr(scraper, "name"):
            raise TypeError(
                f"register() expected a Scraper, got {type(scraper).__name__}"
            )
    sid = getattr(scraper, "source_id", "")
    if not sid:
        raise ValueError("scraper.source_id must be a non-empty string")
    with _LOCK:
        _REGISTRY[sid] = scraper
    logger.info("registered scraper: %s (%s)", sid, getattr(scraper, "name", "?"))


def get(source_id: str) -> Scraper | None:
    """Look up a scraper by id. Returns None if unknown."""
    return _REGISTRY.get(source_id)


def get_all() -> list[Scraper]:
    """Return every registered scraper, sorted by source_id for stability."""
    return sorted(_REGISTRY.values(), key=lambda s: getattr(s, "source_id", ""))


def is_registered(source_id: str) -> bool:
    return source_id in _REGISTRY


def reset() -> None:
    """Drop every registered scraper. Used by tests.

    Also evicts the cached scraper modules from ``sys.modules`` so the
    next :func:`discover_scrapers` call will trigger a fresh import
    and re-run each module's bottom-of-file ``register()`` call.
    """
    global _DISCOVERED
    with _LOCK:
        _REGISTRY.clear()
        _DISCOVERED = False
    # Evict the cached scraper modules so the next discovery actually
    # runs the bottom-of-file ``register()`` instead of pulling a
    # cached module reference from sys.modules.
    prefix = "app.services.scrapers.scrapers."
    for mod_name in list(sys.modules.keys()):
        if mod_name == "app.services.scrapers.scrapers" or mod_name.startswith(prefix):
            sys.modules.pop(mod_name, None)


# ─────────────────────────────────────────────────────────────────────────
# Discovery — walk scrapers/*.py and import each module
# ─────────────────────────────────────────────────────────────────────────


def _scraper_package_dir() -> Path:
    """Return the directory containing the per-scraper .py files."""
    # ``__file__`` of this file is .../app/services/scrapers/registry.py
    return Path(__file__).resolve().parent / "scrapers"


def discover_scrapers(*, force: bool = False) -> list[Scraper]:
    """Auto-import every ``scrapers/*.py`` module and return registered scrapers.

    Mirrors ``app.routers.registry.discover_business_line_routers``: we
    just walk a package directory. Modules are imported by their
    short name; calling them is enough to trigger their bottom-of-file
    ``register()`` call.
    """
    global _DISCOVERED
    with _LOCK:
        if _DISCOVERED and not force:
            return get_all()
        pkg_dir = _scraper_package_dir()
        if not pkg_dir.exists():
            _DISCOVERED = True
            return get_all()
        # Use the package name so relative imports inside the modules work.
        pkg_name = "app.services.scrapers.scrapers"
        # Make sure the package's __init__ has run.
        try:
            importlib.import_module(pkg_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not import scraper package %s: %s", pkg_name, exc)
        for mod_info in pkgutil.iter_modules([str(pkg_dir)]):
            name = mod_info.name
            # Skip private helpers / __init__ / anything that starts with "_".
            if name.startswith("_") or name in {"tests", "test"}:
                continue
            full = f"{pkg_name}.{name}"
            try:
                importlib.import_module(full)
            except Exception as exc:  # noqa: BLE001
                logger.error("failed to import scraper module %s: %s", full, exc)
        _DISCOVERED = True
    return get_all()


# ─────────────────────────────────────────────────────────────────────────
# run_one / run_all
# ─────────────────────────────────────────────────────────────────────────


async def run_one(
    source_id: str,
    *,
    persist: bool = True,
) -> ScraperRunResult | dict[str, Any]:
    """Run a single scraper by id.

    Returns a :class:`ScraperRunResult` plus (optionally) the generated
    ``upload_id`` from ``raw.uploads`` when ``persist=True``.
    """
    discover_scrapers()
    scraper = get(source_id)
    if scraper is None:
        return {
            "source_id": source_id,
            "status": "error",
            "error": f"unknown source_id: {source_id}",
            "rows": 0,
        }
    if not getattr(scraper, "enabled", True):
        return ScraperRunResult(
            source_id=source_id,
            name=getattr(scraper, "name", source_id),
            rows=0,
            status="disabled",
        ).to_dict()
    result = await scraper.run()
    payload: dict[str, Any] = result.to_dict()
    if persist:
        try:
            # Re-derive rows by re-running the pipeline. ``run()`` already
            # returned; we need the actual rows for persistence.
            raw = await scraper.fetch()
        except Exception:
            raw = []
        if not raw:
            # Fallback path was used (or fetch is permanently broken).
            try:
                raw = scraper.fallback()
            except Exception:
                raw = []
        rows = scraper.validate(scraper.parse(raw))
        landing_rows = [scraper.to_landing_row(r) for r in rows]
        try:
            upload_id = await scraper.persist(landing_rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("persist failed for %s: %s", source_id, exc)
            upload_id = None
        payload["upload_id"] = upload_id
    return payload


async def run_all(*, persist: bool = True) -> list[dict[str, Any]]:
    """Run every enabled scraper. Returns per-source dicts."""
    discover_scrapers()
    out: list[dict[str, Any]] = []
    for scraper in get_all():
        if not getattr(scraper, "enabled", True):
            continue
        try:
            res = await run_one(scraper.source_id, persist=persist)
        except Exception as exc:  # noqa: BLE001
            res = {
                "source_id": scraper.source_id,
                "name": getattr(scraper, "name", scraper.source_id),
                "status": "error",
                "error": str(exc),
                "rows": 0,
            }
        if isinstance(res, ScraperRunResult):
            res = res.to_dict()
        out.append(res)
    return out
