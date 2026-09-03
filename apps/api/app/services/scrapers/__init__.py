"""
apps/api/app/services/scrapers/__init__.py

Web-scraping framework for the Fin BP Portal data-integration layer.

Public API:

* :class:`BaseScraper`     — abstract base every scraper inherits from.
* :class:`Scraper`         — runtime protocol duck-typed by the registry.
* :func:`register`         — register a scraper instance.
* :func:`get`              — fetch a single scraper by source_id.
* :func:`get_all`          — list every registered scraper.
* :func:`run_all`          — execute every enabled scraper; returns per-source results.
* :func:`run_one`          — execute a single scraper by source_id.
* :func:`http_get`         — GET with timeout + optional retry.
* :func:`retry_with_backoff` — decorator for transient-failure retries.
* :func:`rate_limit_check` — in-process per-domain throttle.
* :func:`discover_scrapers` — auto-import every ``scrapers/*.py`` module
                              (mirrors the business-line auto-discovery).

The contract for adding a new scraper:

1. Create a new ``.py`` file in
   ``apps/api/app/services/scrapers/scrapers/``.
2. Define a class that inherits from :class:`BaseScraper`.
3. Implement the four lifecycle methods: ``fetch``, ``parse``,
   ``validate``, ``to_landing_row``.
4. At module bottom call :func:`register` with an instance.

No code in this file or in the framework has to change to add new sources.
"""
from __future__ import annotations

from .base import BaseScraper, Scraper, ScraperRunResult
from .registry import (
    discover_scrapers,
    get,
    get_all,
    register,
    run_all,
    run_one,
)
from .utils import http_get, rate_limit_check, reset_rate_limit, retry_with_backoff

__all__ = [
    "BaseScraper",
    "Scraper",
    "ScraperRunResult",
    "register",
    "get",
    "get_all",
    "run_all",
    "run_one",
    "http_get",
    "rate_limit_check",
    "reset_rate_limit",
    "retry_with_backoff",
    "discover_scrapers",
]
