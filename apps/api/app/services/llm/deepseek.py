"""
apps/api/app/services/llm/deepseek.py

DeepSeek Chat Completions backend.

Activates only when the env var DEEPSEEK_API_KEY is set. The factory
``get_llm_backend()`` in ``llm/__init__.py`` checks this var; if missing,
the factory returns the mock backend (possibly wrapped in a
``FallbackBackend``).

This backend follows the OpenAI-compatible chat completions schema, so
no SDK is required — stdlib ``urllib`` is enough.

Behaviour
---------
- On 2xx with valid JSON: return ``choices[0].message.content``.
- On 4xx / 5xx / timeout / network / JSON errors: **RAISE** an exception
  so the upstream FallbackBackend can degrade to mock. We do NOT return
  a friendly-fallback string from this layer (that would mask real
  failures from the operator / frontend ``used_fallback`` flag).
- The system prompt is rendered from ``prompts.render_system_prompt()``
  and cached on the instance. A re-render can be forced by calling
  ``refresh_system_prompt()``.

Configuration (env vars)
-------------------------
- ``DEEPSEEK_API_KEY`` (required) — the API key.
- ``DEEPSEEK_BASE_URL`` (optional) — override the endpoint, default
  ``https://api.deepseek.com/v1/chat/completions``.
- ``DEEPSEEK_MODEL`` (optional) — model name, default ``deepseek-chat``.
- ``DEEPSEEK_TIMEOUT`` (optional) — request timeout in seconds,
  default ``30.0``.
- ``DEEPSEEK_TEMPERATURE`` (optional) — sampling temperature, default
  ``0.3``.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request

from .base import LLMBackend
from .prompts import render_system_prompt


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


DEFAULT_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 30.0
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 1024


def _deepseek_base_url() -> str:
    return os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _deepseek_model() -> str:
    return os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)


def _deepseek_timeout() -> float:
    try:
        return float(os.environ.get("DEEPSEEK_TIMEOUT", str(DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def _deepseek_temperature() -> float:
    try:
        return float(os.environ.get("DEEPSEEK_TEMPERATURE", str(DEFAULT_TEMPERATURE)))
    except (TypeError, ValueError):
        return DEFAULT_TEMPERATURE


# ---------------------------------------------------------------------------
# Exception types (all derive from DeepSeekError for easy catch-all)
# ---------------------------------------------------------------------------


class DeepSeekError(RuntimeError):
    """Base class for all DeepSeek client errors. The FallbackBackend
    catches this type to decide whether to fall back to mock."""


class DeepSeekConfigError(DeepSeekError):
    """Configuration error — missing API key, bad URL, etc."""


class DeepSeekHTTPError(DeepSeekError):
    """HTTP 4xx / 5xx response. Carries the status + parsed body snippet."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"DeepSeek HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class DeepSeekTimeoutError(DeepSeekError):
    """Request timed out."""


class DeepSeekProtocolError(DeepSeekError):
    """Response could not be parsed / missing expected fields."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DeepSeekBackend:
    """DeepSeek Chat Completions backend.

    Uses ``urllib`` (no extra deps). The API follows the OpenAI-compatible
    schema, so no SDK is required.
    """

    name: str = "deepseek"
    model: str = DEFAULT_MODEL

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise DeepSeekConfigError("DEEPSEEK_API_KEY is not set")
        # Cache the system prompt — registry doesn't change at runtime.
        self._system_prompt = render_system_prompt()
        # Refresh-on-demand support (tests can poke the registry).
        self._system_prompt_api_base: str | None = None
        # Stats (read by /api/copilot/health and the frontend).
        self.last_call_status: str | None = None  # "ok" | "error" | "timeout" | None
        self.last_error: str | None = None
        self.last_latency_ms: int | None = None
        self.call_count: int = 0
        self.success_count: int = 0
        # Per-instance model + temperature (so health can report them).
        self.model = _deepseek_model()
        self.temperature = _deepseek_temperature()
        self.timeout = _deepseek_timeout()
        self.base_url = _deepseek_base_url()

    # ── Public surface ─────────────────────────────────────────────────

    def refresh_system_prompt(self) -> None:
        """Force a re-render of the system prompt (tests / registry changes)."""
        self._system_prompt = render_system_prompt()

    async def complete(self, prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """Call DeepSeek and return the assistant's text content.

        Raises:
            DeepSeekConfigError: missing or invalid configuration.
            DeepSeekHTTPError: non-2xx HTTP response.
            DeepSeekTimeoutError: request timed out.
            DeepSeekProtocolError: response is missing expected fields.
            urllib.error.URLError: low-level network failure (DNS, refused, etc).

        The FallbackBackend catches DeepSeekError + URLError to trigger
        degradation. Any other exception propagates unchanged.
        """
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        import time as _time

        started = _time.monotonic()
        self.call_count += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = getattr(resp, "status", 200)
        except urllib.error.HTTPError as exc:
            # 4xx / 5xx — read the body for diagnostics, then raise.
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                err_body = str(exc)
            self.last_call_status = "error"
            self.last_error = f"HTTP {exc.code}"
            self.last_latency_ms = int((_time.monotonic() - started) * 1000)
            raise DeepSeekHTTPError(exc.code, err_body) from exc
        except (TimeoutError, socket.timeout) as exc:
            self.last_call_status = "timeout"
            self.last_error = f"timeout after {self.timeout}s"
            self.last_latency_ms = int((_time.monotonic() - started) * 1000)
            raise DeepSeekTimeoutError(
                f"DeepSeek request timed out after {self.timeout}s"
            ) from exc
        except urllib.error.URLError as exc:
            self.last_call_status = "error"
            self.last_error = f"URLError: {exc.reason}"
            self.last_latency_ms = int((_time.monotonic() - started) * 1000)
            # Wrap as DeepSeekError so the FallbackBackend can catch.
            raise DeepSeekError(f"DeepSeek network error: {exc.reason}") from exc

        # 2xx but maybe still an error body — let JSON parse decide.
        if status >= 400:
            self.last_call_status = "error"
            self.last_error = f"HTTP {status}"
            self.last_latency_ms = int((_time.monotonic() - started) * 1000)
            raise DeepSeekHTTPError(status, raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.last_call_status = "error"
            self.last_error = f"invalid JSON: {exc}"
            self.last_latency_ms = int((_time.monotonic() - started) * 1000)
            raise DeepSeekProtocolError(
                f"DeepSeek returned invalid JSON: {exc}"
            ) from exc

        choices = data.get("choices") or []
        if not choices:
            self.last_call_status = "error"
            self.last_error = "empty choices"
            self.last_latency_ms = int((_time.monotonic() - started) * 1000)
            raise DeepSeekProtocolError("DeepSeek returned empty 'choices'")
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if not content:
            self.last_call_status = "error"
            self.last_error = "empty content"
            self.last_latency_ms = int((_time.monotonic() - started) * 1000)
            raise DeepSeekProtocolError("DeepSeek returned empty 'content'")

        # Success.
        self.last_call_status = "ok"
        self.last_error = None
        self.last_latency_ms = int((_time.monotonic() - started) * 1000)
        self.success_count += 1
        return str(content)

    async def embed(self, text: str) -> list[float]:
        """DeepSeek does not host an embeddings endpoint as of writing;
        return an empty list (the base protocol contract)."""
        return []


__all__ = [
    "DeepSeekBackend",
    "DeepSeekError",
    "DeepSeekConfigError",
    "DeepSeekHTTPError",
    "DeepSeekTimeoutError",
    "DeepSeekProtocolError",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "DEFAULT_TEMPERATURE",
]
