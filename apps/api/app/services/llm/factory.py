"""
apps/api/app/services/llm/factory.py

LLM provider factory — runtime-toggleable via the ``ai_models`` table.

Design
------
Before this module, the active backend was decided by environment
variables: ``DEEPSEEK_API_KEY`` → ``DeepSeekBackend``;
``OLLAMA_BASE_URL`` → ``OllamaBackend``; else ``MockBackend``. That
worked for a single deployment but required an API restart (or a
container rebuild) to switch providers.

The new flow is database-driven. The factory now:

  1. Looks for the row with ``is_default=TRUE AND enabled=TRUE AND
     is_active=TRUE`` in ``ai_models``. If it finds one, the factory
     instantiates the matching provider class from the row's
     ``provider`` / ``model_name`` / ``base_url`` / ``api_key`` columns.
  2. If no default row is configured, falls back to the legacy env
     check (``get_legacy_env_backend``) so existing deployments keep
     working without any admin UI action.
  3. If neither (1) nor (2) yields a provider, returns ``MockBackend``
     so the system is never broken.

``get_active_model()`` is the single read-side helper the rest of the
app should use. It returns a typed dict (the schema mirrors the row)
or None if no default is configured. It NEVER raises — the caller
decides what to do with "no model".

Provider matrix
---------------
The factory currently supports six provider strings (mirroring the SQL
CHECK constraint on ``ai_models.provider``):

  * ``mock``       → ``MockBackend`` (no I/O, deterministic)
  * ``deepseek``   → ``DeepSeekBackend`` (with fallback to mock)
  * ``ollama``     → ``OllamaBackend``
  * ``openai``     → ``OpenAICompatibleBackend`` (base_url optional,
                     defaults to https://api.openai.com/v1/chat/completions)
  * ``anthropic``  → ``OpenAICompatibleBackend`` (anthropic-style base_url)
  * ``custom``     → ``OpenAICompatibleBackend`` (any OpenAI-compatible
                     endpoint; the operator supplies a custom base_url)

The first three use the existing backend classes (no API change for
them). The last three reuse a new ``OpenAICompatibleBackend`` class
that follows the same chat-completions wire format as DeepSeek but is
configurable at runtime (the existing DeepSeek class reads env vars
at construction time, so it can't be reused as-is for a database
configuration).

API-key handling
----------------
The ``api_key`` column stores either an encrypted secret (Fernet, see
``core/secret.py``) or an ``env:VAR_NAME`` reference. ``decrypt_secret``
resolves either form at call time. If both the table column and the
env var are empty, the factory returns a config error (the operator
will see it in the response of the ``/test`` endpoint).
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text

from ...core.logging import get_logger
from ...core.secret import decrypt_secret
from ...db.session import get_session_factory
from .base import LLMBackend
from .deepseek import DeepSeekBackend
from .mock import MockBackend
from .ollama import OllamaBackend

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Active-model row dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AIModelRow:
    """Lightweight typed view of one ``ai_models`` row.

    The factory uses this to instantiate a backend. Only the fields
    the backend needs are stored; everything else (audit timestamps
    etc.) is fetched by the admin API, not by the factory.
    """

    id: int
    name: str
    provider: str
    model_name: str
    base_url: Optional[str]
    api_key: Optional[str]
    enabled: bool
    is_default: bool
    is_active: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "is_active": self.is_active,
        }


# ---------------------------------------------------------------------------
# OpenAI-compatible adapter (used for openai / anthropic / custom)
# ---------------------------------------------------------------------------


class OpenAICompatibleBackend:
    """Generic OpenAI Chat-Completions client.

    Used for the three new provider strings (``openai``, ``anthropic``,
    ``custom``). The wire format is the same as DeepSeek — JSON body
    with ``messages`` / ``model`` / ``temperature`` / ``max_tokens``,
    a Bearer token, and a ``choices[0].message.content`` reply.

    ``anthropic`` is mapped to this backend for the same reason every
    other vendor router does: Anthropic's Messages API is similar but
    uses ``system`` as a top-level field rather than as a messages
    entry. To keep the surface area small, we route the anthropic
    provider through the OpenAI-compatible path, which works against
    any OpenAI-compatible proxy in front of Anthropic (e.g. LiteLLM).
    Pure-Anthropic adapters can be added later as a separate class.
    """

    name: str = "openai-compatible"

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: Optional[str],
        base_url: str,
        temperature: float = 0.3,
        timeout: float = 30.0,
    ) -> None:
        self.provider_label = provider
        self.name = provider
        self.model = model
        self.api_key = api_key or ""
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        # Stats
        self.last_call_status: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_latency_ms: Optional[int] = None
        self.call_count: int = 0
        self.success_count: int = 0

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        system_prompt: Optional[str] = None,
    ) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt or "你是一名金融 BP 业务助手。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }
        payload = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.base_url, data=payload, headers=headers, method="POST"
        )
        self.call_count += 1
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = getattr(resp, "status", 200)
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                err_body = str(exc)
            self.last_call_status = "error"
            self.last_error = f"HTTP {exc.code}"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(
                f"{self.provider_label} HTTP {exc.code}: {err_body[:200]}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            self.last_call_status = "timeout"
            self.last_error = f"timeout after {self.timeout}s"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise TimeoutError(
                f"{self.provider_label} request timed out after {self.timeout}s"
            ) from exc
        except urllib.error.URLError as exc:
            self.last_call_status = "error"
            self.last_error = f"URLError: {exc.reason}"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(
                f"{self.provider_label} network error: {exc.reason}"
            ) from exc

        if status >= 400:
            self.last_call_status = "error"
            self.last_error = f"HTTP {status}"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(f"{self.provider_label} HTTP {status}: {raw[:200]}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.last_call_status = "error"
            self.last_error = f"invalid JSON: {exc}"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(
                f"{self.provider_label} returned invalid JSON: {exc}"
            ) from exc
        choices = data.get("choices") or []
        if not choices:
            self.last_call_status = "error"
            self.last_error = "empty choices"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(f"{self.provider_label} returned empty 'choices'")
        content = ((choices[0] or {}).get("message") or {}).get("content")
        if not content:
            self.last_call_status = "error"
            self.last_error = "empty content"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(f"{self.provider_label} returned empty 'content'")
        self.last_call_status = "ok"
        self.last_error = None
        self.last_latency_ms = int((time.monotonic() - started) * 1000)
        self.success_count += 1
        return str(content)

    async def embed(self, text: str) -> list[float]:
        # No-op — the OpenAI-compatible adapter only supports chat completions.
        return []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


# Default base URLs for built-in providers. Operators can override via
# the ``base_url`` column in the ``ai_models`` table.
_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "ollama": "http://localhost:11434/api/chat",
    "custom": "",
    "mock": "",
}


def get_active_model() -> Optional[AIModelRow]:
    """Return the row that the factory should use right now.

    Selection rules, in order:
      1. ``is_default=TRUE AND enabled=TRUE AND is_active=TRUE`` — the
         most recently updated default wins if more than one row
         somehow has ``is_default=TRUE`` (a defensive tie-breaker — the
         API enforces uniqueness on write).
      2. The first ``enabled=TRUE AND is_active=TRUE`` row sorted by
         ``id ASC``. This is the recovery path if every default was
         accidentally cleared.

    Returns None if the table is empty OR the DB is unreachable. The
    factory then falls back to the env-var path.

    Safe to call from any context (sync bootstrap, sync engine init,
    inside a running asyncio loop in a request handler). Implementation
    note: we always run the async query in a fresh worker thread so we
    never collide with a running loop and never block a handler that's
    already on the loop.
    """
    try:
        get_session_factory()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_active_model: session factory unavailable: %s", exc)
        return None
    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-model-read") as ex:
            future = ex.submit(_run_async_in_worker, _fetch_active_row())
            return future.result(timeout=5)
    except FutTimeout:
        logger.warning("get_active_model: DB lookup timed out after 5s")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_active_model: DB lookup failed: %s", exc)
        return None


def _run_async_in_worker(coro):
    """Helper for ``get_active_model``: run ``coro`` in a fresh event
    loop on the current (worker) thread. We use a tiny wrapper so the
    ``ThreadPoolExecutor.submit`` line above stays readable.
    """
    import asyncio
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # ``asyncio.run`` couldn't get a loop in this thread (e.g. the
        # worker thread is shutting down). Try the explicit new-loop
        # form, then close it.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# Kept for backwards-compat: some legacy tests import it. It is a
# no-op shim now that ``get_active_model`` is fully sync.
def _fetch_active_row_sync() -> Optional[AIModelRow]:
    return get_active_model()


async def _fetch_active_row() -> Optional[AIModelRow]:
    """Async helper for ``get_active_model`` — kept separate so tests
    can await it directly.
    """
    factory = get_session_factory()
    async with factory() as session:
        # Try the default first
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, name, provider, model_name, base_url, api_key,
                           enabled, is_default, is_active
                    FROM ai_models
                    WHERE is_default = TRUE
                      AND enabled = TRUE
                      AND is_active = TRUE
                    ORDER BY updated_at DESC, id ASC
                    LIMIT 1
                    """
                )
            )
        ).mappings().first()
        if row:
            return _row_from_mapping(row)
        # Fallback: any enabled+active row
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, name, provider, model_name, base_url, api_key,
                           enabled, is_default, is_active
                    FROM ai_models
                    WHERE enabled = TRUE AND is_active = TRUE
                    ORDER BY id ASC
                    LIMIT 1
                    """
                )
            )
        ).mappings().first()
        if row:
            return _row_from_mapping(row)
        return None


def _row_from_mapping(m: Any) -> AIModelRow:
    """Build an ``AIModelRow`` from a SQLAlchemy row-mapping."""
    return AIModelRow(
        id=int(m["id"]),
        name=str(m["name"]),
        provider=str(m["provider"]),
        model_name=str(m["model_name"]),
        base_url=m["base_url"],
        api_key=m["api_key"],
        enabled=bool(m["enabled"]),
        is_default=bool(m["is_default"]),
        is_active=bool(m["is_active"]),
    )


# ---------------------------------------------------------------------------
# Backend construction
# ---------------------------------------------------------------------------


def _build_backend_for_row(row: AIModelRow) -> LLMBackend:
    """Instantiate a backend that matches ``row.provider``.

    Raises ``ValueError`` for unknown providers (the API layer turns
    that into a 502 with the message in ``detail``). For deepseek
    and ollama, the existing backend classes are used; their
    constructors read env vars at __init__ time, so we temporarily
    mutate os.environ to point them at the row's model + base_url.
    """
    provider = row.provider.lower()
    base_url = row.base_url or _DEFAULT_BASE_URLS.get(provider, "")
    api_key = decrypt_secret(row.api_key)

    if provider == "mock":
        return MockBackend()

    if provider == "deepseek":
        if not api_key:
            raise ValueError("deepseek provider requires an api_key")
        # The existing DeepSeekBackend reads DEEPSEEK_API_KEY /
        # DEEPSEEK_BASE_URL / DEEPSEEK_MODEL at __init__ time. We push
        # the row's values into the environment for the call, then
        # construct the backend. The override is process-scoped (not
        # thread-scoped); a concurrent request may pick up a different
        # value. This is acceptable for a low-traffic admin endpoint
        # and matches the previous env-driven design.
        return _with_env(
            {
                "DEEPSEEK_API_KEY": api_key,
                "DEEPSEEK_BASE_URL": base_url or "https://api.deepseek.com/v1/chat/completions",
                "DEEPSEEK_MODEL": row.model_name,
            },
            lambda: DeepSeekBackend(),
        )

    if provider == "ollama":
        # Ollama is local + no auth; the env push is enough.
        return _with_env(
            {
                "OLLAMA_BASE_URL": base_url or "http://localhost:11434",
                "OLLAMA_MODEL": row.model_name,
            },
            lambda: OllamaBackend(),
        )

    if provider in {"openai", "anthropic", "custom"}:
        if provider != "anthropic" and not base_url:
            raise ValueError(
                f"{provider} provider requires a non-empty base_url"
            )
        if not base_url:
            base_url = _DEFAULT_BASE_URLS[provider]
        if provider == "openai" and not api_key:
            # OpenAI is auth-required; raise a clear error.
            raise ValueError("openai provider requires an api_key")
        return OpenAICompatibleBackend(
            provider=provider,
            model=row.model_name,
            api_key=api_key,
            base_url=base_url,
        )

    raise ValueError(f"unknown provider: {provider!r}")


def _with_env(values: dict[str, str], build: Any) -> Any:
    """Set ``values`` in ``os.environ`` for the duration of ``build()``.

    Restores the previous values (or deletes the keys if they didn't
    exist) on the way out, even if ``build`` raises. This is the
    same pattern the rest of the codebase uses (e.g. the existing
    ``get_llm_backend`` function).
    """
    saved: dict[str, Optional[str]] = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            os.environ[k] = v
        return build()
    finally:
        for k, prev in saved.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


# ---------------------------------------------------------------------------
# Public factory entry points
# ---------------------------------------------------------------------------


def get_llm_backend() -> LLMBackend:
    """Pick the active LLM backend at runtime.

    Resolution order (mirrors the docstring at the top of this file):
      1. ``ai_models`` default row (DB-driven)
      2. Env-var fallback (``get_legacy_env_backend``)
      3. ``MockBackend`` — the "always works" tail
    """
    row = get_active_model()
    if row is not None:
        try:
            backend = _build_backend_for_row(row)
            logger.info(
                "get_llm_backend: using db-configured model id=%s name=%s provider=%s",
                row.id, row.name, row.provider,
            )
            return backend
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_llm_backend: db-configured model id=%s failed to build: %s; "
                "falling back to env/mock", row.id, exc,
            )
    legacy = get_legacy_env_backend()
    if legacy is not None:
        return legacy
    return MockBackend()


def get_legacy_env_backend() -> Optional[LLMBackend]:
    """The pre-registry env-var path. Returns None if no env is set.

    Kept as a separate function so the test suite can exercise both
    paths independently, and so the admin UI can render a "you're
    using the env-var fallback" banner by checking whether
    ``get_active_model()`` is None.
    """
    if os.environ.get("DEEPSEEK_API_KEY"):
        try:
            return DeepSeekBackend()
        except Exception:  # noqa: BLE001
            return None
    if os.environ.get("OLLAMA_BASE_URL"):
        try:
            return OllamaBackend()
        except Exception:  # noqa: BLE001
            return None
    return None


def configured_backend_name() -> str:
    """Return the *configured* backend name (no I/O).

    Mirrors the legacy function in ``__init__.py`` but goes through
    the DB first. Falls back to the env-var name for the case where
    no default row is configured.
    """
    row = get_active_model()
    if row is not None:
        return row.provider
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("OLLAMA_BASE_URL"):
        return "ollama"
    return "mock"


def get_primary_backend() -> LLMBackend:
    """Return the configured primary backend (no FallbackBackend wrapper)."""
    return get_llm_backend()


__all__ = [
    "AIModelRow",
    "OpenAICompatibleBackend",
    "configured_backend_name",
    "get_active_model",
    "get_legacy_env_backend",
    "get_llm_backend",
    "get_primary_backend",
]
