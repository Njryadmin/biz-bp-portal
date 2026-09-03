"""
apps/api/app/services/llm/__init__.py

LLM backend factory + fallback chain.

The factory picks the backend in priority order:

  1. ``ai_models`` registry  — runtime-toggleable via the admin UI.
     The row with ``is_default=TRUE AND enabled=TRUE AND is_active=TRUE``
     wins. See ``factory.get_active_model`` for the full selection logic.
  2. ``DEEPSEEK_API_KEY`` set  →  ``DeepSeekBackend``  (primary)
                                  wrapped in ``FallbackBackend`` with mock
                                  (legacy env-var path)
  3. ``OLLAMA_BASE_URL`` set   →  ``OllamaBackend``    (primary)
                                  wrapped in ``FallbackBackend`` with mock
                                  (legacy env-var path)
  4. (default)                 →  ``MockBackend``       (no fallback needed)

The factory is a thin wrapper around the env check; it does not cache
the backend instance (each call returns a fresh object so tests can
monkey-patch env vars). If you need a process-wide singleton, wrap the
factory call at the call site.

This module preserves the public symbols the rest of the codebase
imports (``get_llm_backend``, ``configured_backend_name``,
``get_primary_backend``, the ``FallbackBackend`` class, the
backend classes, etc.). The actual factory logic now lives in
``factory.py``; this file re-exports it for backward compatibility.

Fallback semantics
------------------
``FallbackBackend`` runs the primary first. If the primary raises a
``DeepSeekError``, ``urllib.error.URLError``, or any other Exception,
it transparently falls back to the mock backend. The engine can then
inspect ``backend.used_fallback`` and ``backend.last_error`` to surface
a warning to the user (``used_fallback: true`` in the response).
"""
from __future__ import annotations

import os
from typing import Any

from .base import LLMBackend
from .deepseek import DeepSeekBackend
from .factory import (
    configured_backend_name,
    get_active_model,
    get_llm_backend,
    get_primary_backend,
)
from .mock import MockBackend, MockAnswer
from .ollama import OllamaBackend


# ---------------------------------------------------------------------------
# FallbackBackend — wraps a primary + a fallback backend
# ---------------------------------------------------------------------------


class FallbackBackend:
    """Composite backend: try primary, fall back to mock on error.

    Attributes
    ----------
    name : str
        Mirrors the primary's name. The UI badge shows this so the user
        knows which LLM they intended to use.
    primary_name : str
        Same as ``name``; kept as an alias for clarity.
    primary : LLMBackend
        The configured primary (deepseek or ollama).
    fallback : MockBackend
        The mock backend used when the primary fails.
    used_fallback : bool
        Set after each ``complete()`` call. ``True`` if the response
        came from the fallback.
    last_error : str | None
        Set after each ``complete()`` call. Human-readable reason for
        the fallback (or ``None`` on success).
    last_answer : MockAnswer | None
        Set when the fallback fired AND we recovered a structured
        ``MockAnswer`` (citations, chart) from the mock. ``None`` when
        the primary succeeded.
    primary_stats : dict
        Snapshot of the primary's recent-call stats (model, temperature,
        last_call_status, call_count, success_count, last_latency_ms).
    """

    name: str
    primary_name: str

    def __init__(self, primary: LLMBackend, fallback: MockBackend) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name
        self.primary_name = primary.name
        # Mutable per-instance state.
        self.used_fallback: bool = False
        self.last_error: str | None = None
        self.last_answer: MockAnswer | None = None
        self.primary_stats: dict[str, Any] = {}

    # ── LLMBackend surface ─────────────────────────────────────────────

    async def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        """Try primary, fall back to mock on any exception.

        Returns a text response. May be from the primary or from the
        mock — check ``self.used_fallback`` afterwards.
        """
        # Reset per-call state.
        self.used_fallback = False
        self.last_error = None
        self.last_answer = None
        try:
            text = await self.primary.complete(prompt, max_tokens=max_tokens)
            # Pull primary stats (if it tracks them).
            self._capture_primary_stats()
            return text
        except Exception as exc:  # noqa: BLE001 — broad catch is intentional
            # Capture primary state, then fall back.
            self._capture_primary_stats()
            self.used_fallback = True
            self.last_error = f"{type(exc).__name__}: {exc}"
            # Try to get a structured MockAnswer (citations + chart) by
            # asking the mock to answer. We re-derive the question from
            # the prompt — the mock's _extract_user_question handles
            # common delimiters.
            try:
                question = MockBackend._extract_user_question(prompt)
                mock_answer = self.fallback.answer(question)
                # Decorate with fallback metadata.
                mock_answer.debug = dict(mock_answer.debug or {})
                mock_answer.debug["used_fallback"] = True
                mock_answer.debug["fallback_reason"] = self.last_error
                mock_answer.debug["configured_backend"] = self.primary_name
                self.last_answer = mock_answer
                return mock_answer.answer
            except Exception as mock_exc:  # noqa: BLE001
                # Even the mock failed — return a very simple text.
                self.last_error = (
                    f"{self.last_error}; mock fallback also failed: "
                    f"{type(mock_exc).__name__}: {mock_exc}"
                )
                return (
                    f"[Copilot 不可用 — 配置后端 {self.primary_name} 调用失败: {exc};"
                    f" mock 后备也失败: {mock_exc}]"
                )

    async def embed(self, text: str) -> list[float]:
        """Try primary, fall back to mock on error."""
        try:
            return await self.primary.embed(text)
        except Exception:  # noqa: BLE001
            return await self.fallback.embed(text)

    # ── Helpers ────────────────────────────────────────────────────────

    def _capture_primary_stats(self) -> None:
        """Snapshot the primary's counters + last status (for /health)."""
        p = self.primary
        stats: dict[str, Any] = {
            "name": getattr(p, "name", "unknown"),
        }
        # DeepSeek and Ollama both expose `model` and (DeepSeek)
        # `temperature` + counters. Capture anything that's there.
        for attr in (
            "model",
            "temperature",
            "timeout",
            "base_url",
            "last_call_status",
            "last_error",
            "last_latency_ms",
            "call_count",
            "success_count",
        ):
            if hasattr(p, attr):
                stats[attr] = getattr(p, attr)
        self.primary_stats = stats


# Note: ``get_llm_backend`` / ``configured_backend_name`` /
# ``get_primary_backend`` are re-exported above from ``factory``.
# The factory now consults the ``ai_models`` table first and falls
# back to the env-var logic below (see ``factory.get_legacy_env_backend``)
# before resorting to the MockBackend. This preserves the original
# module-level surface (importing these names from
# ``app.services.llm`` still works) while adding the new registry path.


__all__ = [
    "LLMBackend",
    "MockBackend",
    "MockAnswer",
    "DeepSeekBackend",
    "OllamaBackend",
    "FallbackBackend",
    "get_active_model",
    "get_llm_backend",
    "configured_backend_name",
    "get_primary_backend",
]
