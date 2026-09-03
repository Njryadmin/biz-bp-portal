"""
apps/api/app/services/scrapers/utils.py

Shared HTTP / retry / rate-limit utilities for the scraper framework.

* :func:`http_get`              — sync GET with sane defaults.
* :func:`retry_with_backoff`    — decorator for exponential backoff.
* :func:`rate_limit_check`      — in-process per-domain throttle.

The framework avoids async-only HTTP to keep the parser code easy to
read; scrapers that need concurrency can wrap calls in ``asyncio.gather``
themselves. ``httpx`` is already a project dependency.
"""
from __future__ import annotations

import functools
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable, TypeVar

import httpx

from ...core.logging import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ─────────────────────────────────────────────────────────────────────────
# http_get
# ─────────────────────────────────────────────────────────────────────────


def http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    params: dict[str, Any] | None = None,
    follow_redirects: bool = True,
) -> httpx.Response:
    """Synchronous GET with sensible defaults.

    * Default User-Agent set to a normal browser string (some sites
      block Python/httpx by default).
    * Raises ``httpx.HTTPError`` on non-2xx by default; callers can
      check ``.status_code`` themselves before raising.
    """
    merged_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if headers:
        merged_headers.update(headers)
    with httpx.Client(
        timeout=timeout,
        follow_redirects=follow_redirects,
        headers=merged_headers,
    ) as client:
        return client.get(url, params=params)


# ─────────────────────────────────────────────────────────────────────────
# retry_with_backoff
# ─────────────────────────────────────────────────────────────────────────


def retry_with_backoff(
    fn: F | None = None,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (httpx.HTTPError, ConnectionError, TimeoutError),
) -> F:
    """Decorator: exponential backoff retry.

    Usage:

        @retry_with_backoff
        def fetch_page(url): ...

        @retry_with_backoff(max_retries=5, base_delay=2)
        async def fetch_async(...): ...

    Sleep schedule: ``base_delay * 2**attempt`` capped at ``max_delay``.
    """

    def _wrap(target: F) -> F:
        @functools.wraps(target)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return target(*args, **kwargs)
                except retry_on as exc:  # noqa: PERF203
                    last_exc = exc
                    if attempt >= max_retries:
                        break
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "%s failed (attempt %d/%d): %s — sleeping %.2fs",
                        getattr(target, "__name__", "callable"),
                        attempt + 1,
                        max_retries + 1,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        @functools.wraps(target)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await target(*args, **kwargs)  # type: ignore[misc]
                except retry_on as exc:  # noqa: PERF203
                    last_exc = exc
                    if attempt >= max_retries:
                        break
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "%s failed (attempt %d/%d): %s — sleeping %.2fs",
                        getattr(target, "__name__", "callable"),
                        attempt + 1,
                        max_retries + 1,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        import inspect

        if inspect.iscoroutinefunction(target):
            return _async_wrapper  # type: ignore[return-value]
        return _sync_wrapper  # type: ignore[return-value]

    # Support both ``@retry_with_backoff`` and ``@retry_with_backoff(max_retries=5)``.
    if fn is not None and callable(fn):
        return _wrap(fn)
    return _wrap  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────
# rate_limit_check
# ─────────────────────────────────────────────────────────────────────────


class _DomainLimiter:
    """Per-domain sliding-window throttle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, domain: str, max_per_minute: int) -> bool:
        """Record one hit. Returns True if allowed, False if over budget."""
        now = time.monotonic()
        window = 60.0
        with self._lock:
            dq = self._hits[domain]
            cutoff = now - window
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= max_per_minute:
                return False
            dq.append(now)
            return True

    def reset(self, domain: str | None = None) -> None:
        with self._lock:
            if domain is None:
                self._hits.clear()
            else:
                self._hits.pop(domain, None)


_limiter = _DomainLimiter()


def rate_limit_check(domain: str, max_per_minute: int = 10) -> bool:
    """In-process per-domain rate limit. Returns True if the call may proceed.

    For scraping inside the FastAPI process this is a polite defense
    against accidentally hammering a site. For distributed scraping you
    would swap this for a Redis-backed token bucket — the function
    signature is intentionally narrow so the swap is one-line.
    """
    return _limiter.hit(domain, max_per_minute)


def reset_rate_limit(domain: str | None = None) -> None:
    """Reset the limiter (used by tests)."""
    _limiter.reset(domain)


def _domain_of(url: str) -> str:
    """Best-effort hostname extraction without pulling in urllib.parse on every call."""
    s = url
    for prefix in ("http://", "https://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    slash = s.find("/")
    return s[:slash] if slash >= 0 else s
