"""
apps/api/app/services/copilot_engine.py

The Copilot Engine — turns a free-form Finance BP question into a
structured answer with citations pointing at real data.

ARCHITECTURE
============

    ┌──────────────┐    ┌──────────────────────────┐    ┌────────────────────┐
    │ CopilotReq   │ -> │ CopilotEngine.ask()      │ -> │ CopilotResponse    │
    │  question    │    │  1. pick LLM backend     │    │  answer            │
    │  line_id?    │    │  2. mock: dispatch       │    │  citations[]       │
    │  ctx_lines?  │    │  3. real: prompt+complete │    │  chart_data?       │
    └──────────────┘    │  4. wrap in response     │    │  intent,confidence │
                        └──────────────────────────┘    │  backend,debug     │
                                                        │  used_fallback?    │
                                                        └────────────────────┘

The engine is INTENTIONALLY GENERIC: it does NOT import any
``business_lines/*`` code. It works exclusively through:

- ``apps.api.app.core.registry.load_registry()`` for line metadata.
- HTTP calls to the running API (e.g. ``GET /api/lines/{line}/projects``).
  The base URL is ``FIN_BP_API_BASE`` (default http://localhost:8769).
- The mock backend's helper functions, which do the same HTTP calls.

A new business line that exposes ``/projects`` (or ``/properties``) and
``/indicators`` will be discovered automatically by the suggestions
endpoint and answered by the mock engine — no code change required.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..core.logging import get_logger
from ..core.registry import load_registry
from .llm import (
    FallbackBackend,
    MockBackend,
    configured_backend_name,
    get_llm_backend,
    get_primary_backend,
)
from .llm.mock import parse_question
from .llm.prompts import build_prompt

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A traceable reference to a real data source.

    The frontend renders one card per citation. ``url`` is optional and
    points back to a UI page in the dashboard.
    """

    model_config = ConfigDict(extra="forbid")

    source: str  # e.g. "business_lines/residential/api/router.py:GET /projects"
    title: str  # e.g. "PRJ-001 上海·绿城黄浦江"
    snippet: str  # 1-2 line excerpt
    url: str | None = None


class CopilotRequest(BaseModel):
    """POST body for /api/copilot/ask."""

    model_config = ConfigDict(extra="forbid")

    # Allow empty string here; the router validates and returns 400 with
    # a friendly message. Pydantic's min_length=1 would surface as 422
    # which is less useful for end users.
    question: str = Field(..., max_length=2000)
    line_id: str | None = None  # optional: restrict to one business line
    context_lines: list[str] | None = None  # optional: restrict to a subset
    # UI toggle: True = try to use the real (deepseek/ollama) backend,
    # False = force mock. None = use whatever the factory picked from
    # env. Honored only when the relevant env var is set; otherwise the
    # factory is the source of truth.
    prefer_real_llm: bool | None = None


class CopilotResponse(BaseModel):
    """Full response of /api/copilot/ask."""

    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    chart_data: dict[str, Any] | None = None
    intent: str
    confidence: float
    backend: str
    # Resolved line id (from question parsing or request). Always populated
    # so the frontend can read `response.line_id` directly.
    line_id: str | None = None
    # New fields (additive, all Optional with defaults — old clients
    # still work).
    used_fallback: bool = False
    fallback_reason: str | None = None
    model: str | None = None
    debug: dict[str, Any] | None = None


class CopilotHealth(BaseModel):
    """GET /api/copilot/health response."""

    model_config = ConfigDict(extra="forbid")

    backend: str
    available_lines: list[str]
    api_base: str
    # New fields
    configured_backend: str  # the backend env says we should use
    deepseek_key_present: bool = False
    ollama_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    used_fallback: bool = False
    last_call_status: str | None = None  # "ok" | "error" | "timeout" | None
    last_error: str | None = None
    last_latency_ms: int | None = None
    call_count: int = 0
    success_count: int = 0
    primary_stats: dict[str, Any] | None = None


class CopilotSuggestions(BaseModel):
    """GET /api/copilot/suggestions response."""

    model_config = ConfigDict(extra="forbid")

    by_line: dict[str, list[str]]  # line_id -> list of suggested questions
    common: list[str]  # cross-line suggestions


# ---------------------------------------------------------------------------
# Pre-defined suggestions
# ---------------------------------------------------------------------------
#
# These are the "starter" questions shown in the UI. The engine does not
# try to be exhaustive — the goal is to demonstrate the breadth of
# intents the mock backend understands.
# ---------------------------------------------------------------------------


COMMON_SUGGESTIONS: list[str] = [
    "三业务线 KPI 概览对比",
    "做一份敏感性分析",
    "全公司有哪些业务线",
]


# ---------------------------------------------------------------------------
# Dynamic per-line suggestion builder.
# ---------------------------------------------------------------------------
#
# Originally the suggestion table was hardcoded for 3 lines. With 10
# lines (and growing) it has to come from the registry + indicators.yaml
# — otherwise new lines fall through to the "common" bucket and the
# sidebar shows no targeted suggestions for them.
#
# We build 4 questions per line, entirely templated so the same logic
# works for any future line:
#
#   1. <line.display_name> 的核心 KPI 概览    (uses first indicator title)
#   2. 对 {line.display_name} 做一份敏感性分析
#   3. 对 {line.display_name} 做未来 12 期预测
#   4. 检查 {line.display_name} 是否有告警
#
# The first indicator is taken from indicators.yaml (the "headline KPI"
# for that line) — e.g. "IRR" for residential, "NOI" for retail,
# "report_count" for valuation. If no indicators.yaml is present, we
# fall back to "核心指标" as a generic placeholder.
# ---------------------------------------------------------------------------


def _first_indicator_title(line_id: str) -> str:
    """Return the title of the first indicator for ``line_id``, or a
    generic placeholder when indicators.yaml is missing or empty.

    Mirrors ``build_line_keywords_from_registry`` in mock.py: both
    files are cheap, independent fallbacks for "what's the headline
    KPI for this line?".
    """
    try:
        from ..core.registry import load_registry

        for entry in load_registry():
            if entry.line.id == line_id and entry.indicators:
                return entry.indicators[0].title or entry.indicators[0].id
    except Exception:  # noqa: BLE001 — defensive
        return "核心指标"
    return "核心指标"


def _line_display_name(line_id: str, fallback: str | None = None) -> str:
    """Resolve a human-readable name for a line.

    Tries registry first, then the ``fallback`` argument, then the
    ``line_id`` itself. Used so a missing registry still produces a
    sensible Chinese display name when running outside the project
    (e.g. legacy unit tests).
    """
    try:
        from ..core.registry import load_registry

        for entry in load_registry():
            if entry.line.id == line_id:
                return entry.line.name or line_id
    except Exception:  # noqa: BLE001
        pass
    return fallback or line_id


def build_line_suggestions() -> dict[str, list[str]]:
    """Build {line_id: [question, ...]} dynamically from the registry.

    Always returns 3-4 questions per registered line, in Chinese, with
    the line's display name + headline KPI interpolated. Future lines
    added to ``business_lines/registry.yaml`` are picked up
    automatically — no edits here required.
    """
    out: dict[str, list[str]] = {}
    try:
        from ..core.registry import load_registry

        entries = load_registry()
    except Exception:  # noqa: BLE001
        return out
    for entry in entries:
        lid = entry.line.id
        name = entry.line.name or lid
        kpi_title = _first_indicator_title(lid)
        out[lid] = [
            f"{name} 的核心 KPI({kpi_title})概览",
            f"对 {name} 做一份敏感性分析",
            f"对 {name} 做未来 12 期预测",
            f"检查 {name} 是否有告警",
        ]
    return out


# Module-level cache: built once at import. Tests that mutate the
# registry can call ``build_line_suggestions.cache_clear()`` (below)
# or ``reset_line_suggestions_cache()`` to rebuild.
_LINE_SUGGESTIONS: dict[str, list[str]] = build_line_suggestions()


def line_suggestions() -> dict[str, list[str]]:
    """Return the cached per-line suggestions. Indirection so tests
    can patch the cache without touching the build function."""
    return _LINE_SUGGESTIONS


def reset_line_suggestions_cache() -> None:
    """Rebuild the module-level suggestions cache from the current
    registry. Production code does NOT need to call this — the cache
    is built once at import time, which is the right behaviour for
    the long-running uvicorn process."""
    global _LINE_SUGGESTIONS
    _LINE_SUGGESTIONS = build_line_suggestions()
    globals()["LINE_SUGGESTIONS"] = _LINE_SUGGESTIONS


# Backwards-compat alias — the legacy module-level ``LINE_SUGGESTIONS``
# dict. Kept as a property of the module so existing imports keep
# working. Read-only.
LINE_SUGGESTIONS: dict[str, list[str]] = _LINE_SUGGESTIONS


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_question(q: str) -> str | None:
    """Return a short error message if the question is invalid, else None.

    Empty / whitespace-only → "question is required".
    Excessive length → caught by Pydantic max_length=2000; we keep this
    function as a single source of truth.
    """
    if q is None:
        return "question is required"
    q = q.strip()
    if not q:
        return "question is required"
    if len(q) > 2000:
        return "question exceeds 2000 chars"
    return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class CopilotEngine:
    """Top-level orchestrator. Stateless — instantiate per request.

    In production a single instance is fine (no I/O at __init__), but
    instantiating per request is also OK because the heavy HTTP work
    happens in the backend, not here.
    """

    def __init__(self) -> None:
        # The default backend is the env-driven factory choice. The
        # ``prefer_real_llm`` field on each request can override this
        # in ``ask()`` by re-picking the backend.
        self._backend = get_llm_backend()
        # Per-request active user, set by the HTTP router after
        # auth resolution. The system prompt will mention the user so
        # the LLM can answer "who is asking" questions. Cleared on
        # every new CopilotEngine() because engines are per-request.
        self._active_user: dict[str, Any] | None = None

    def set_active_user(self, user: dict[str, Any] | None) -> None:
        """Inject the currently authenticated user (id/username/roles/
        accessible_lines) into the engine so the LLM system prompt
        can mention who is asking.

        The router calls this after `get_current_user` resolves the
        cookie. The user dict is the public projection of
        ``CurrentUser``.
        """
        self._active_user = user

    def active_user(self) -> dict[str, Any] | None:
        return self._active_user

    def _pick_backend(self, prefer_real_llm: bool | None) -> Any:
        """Pick a backend honoring the user toggle.

        Rules:
          - ``None`` (default): use the env-driven factory choice.
          - ``True``: try to use a real LLM. If env has DEEPSEEK_API_KEY
            or OLLAMA_BASE_URL, return a fresh FallbackBackend. Else
            fall back to MockBackend (toggle can't be honored).
          - ``False``: force MockBackend, regardless of env.

        Note: every call returns a fresh backend instance so per-request
        state (used_fallback, last_error, call counters) doesn't leak
        across requests.
        """
        if prefer_real_llm is False:
            return MockBackend()
        if prefer_real_llm is True:
            primary = get_primary_backend()
            if isinstance(primary, MockBackend):
                # No real backend configured — honor is impossible.
                return MockBackend()
            return FallbackBackend(primary, MockBackend())
        # None → default
        return get_llm_backend()

    # ── Public API ─────────────────────────────────────────────────────

    def ask(self, req: CopilotRequest) -> CopilotResponse:
        """Run the full ask pipeline. Returns a CopilotResponse.

        For the mock backend, this is mostly synchronous (one call to
        ``MockBackend.answer()``). For real backends, this calls the LLM
        and parses citations from the response.
        """
        question = req.question.strip()

        # Resolve the effective backend for this request, honoring the
        # ``prefer_real_llm`` toggle from the UI.
        effective_backend = self._pick_backend(req.prefer_real_llm)

        # Merge optional line_id into the question so the parser sees it.
        # This makes the same intent template work whether the line is
        # explicit ("/residential ...") or implicit ("住宅 ...").
        effective_question = self._maybe_inject_line(question, req.line_id)

        # Pre-parse: get intent/line/top_n/threshold for debug + citation routing.
        parsed = parse_question(effective_question)

        if isinstance(effective_backend, MockBackend):
            # Pass the explicit line_id (if any) so the mock helper can
            # target it even if the in-question parser didn't pick it up.
            # Example: line_id="tmp-line" + question="这个 line 的指标"
            # → parsed.line is None, but req.line_id is "tmp-line".
            mock_answer = effective_backend.answer(
                effective_question,
                line_override=req.line_id,
            )
            return self._build_response_from_mock(
                question=question,
                mock=mock_answer,
                parsed=parsed,
                request_line=req.line_id,
                backend_name=effective_backend.name,
            )

        # Real backend (or FallbackBackend wrapping a real one).
        return self._ask_real_llm(
            req, parsed, effective_question, backend_override=effective_backend
        )

    def health(self) -> CopilotHealth:
        entries = load_registry()
        backend = self._backend
        # Drill into FallbackBackend for primary stats.
        primary = backend.primary if isinstance(backend, FallbackBackend) else backend
        # Gather stats.
        deepseek_key_present = bool(os.environ.get("DEEPSEEK_API_KEY"))
        ollama_url = os.environ.get("OLLAMA_BASE_URL")
        model = getattr(primary, "model", None)
        temperature = getattr(primary, "temperature", None)
        last_call_status = getattr(primary, "last_call_status", None)
        last_error = getattr(primary, "last_error", None)
        last_latency_ms = getattr(primary, "last_latency_ms", None)
        call_count = getattr(primary, "call_count", 0)
        success_count = getattr(primary, "success_count", 0)
        primary_stats: dict[str, Any] | None = None
        if isinstance(backend, FallbackBackend):
            primary_stats = backend.primary_stats or None
        return CopilotHealth(
            backend=backend.name,
            available_lines=[e.line.id for e in entries],
            api_base=os.environ.get("FIN_BP_API_BASE", "http://localhost:8769"),
            configured_backend=configured_backend_name(),
            deepseek_key_present=deepseek_key_present,
            ollama_url=ollama_url,
            model=model,
            temperature=temperature,
            used_fallback=getattr(backend, "used_fallback", False) if isinstance(backend, FallbackBackend) else False,
            last_call_status=last_call_status,
            last_error=last_error,
            last_latency_ms=last_latency_ms,
            call_count=call_count,
            success_count=success_count,
            primary_stats=primary_stats,
        )

    def suggestions(self) -> CopilotSuggestions:
        # ``line_suggestions()`` is already filtered to registered lines
        # (it is built from ``load_registry()``). The defensive filter
        # below is a no-op in production but keeps the contract narrow:
        # we never leak suggestions for a line that was unregistered
        # between module-import time and the request.
        entries = load_registry()
        registered = {e.line.id for e in entries}
        by_line: dict[str, list[str]] = {
            lid: qs for lid, qs in line_suggestions().items() if lid in registered
        }
        return CopilotSuggestions(by_line=by_line, common=COMMON_SUGGESTIONS)

    def suggestions_for_user(
        self, *, roles: list[str], accessible_lines: list[str]
    ) -> CopilotSuggestions:
        """Like ``suggestions()`` but filtered to the user's accessible lines.

        Rules:
          * admin / viewer / auditor → see every registered line
          * bp:<line>                → only that line
          * multiple roles are unioned
        """
        entries = load_registry()
        registered = {e.line.id for e in entries}
        all_sug = line_suggestions()
        is_global = any(r in roles for r in ("admin", "auditor", "viewer"))
        if is_global:
            allowed_ids = set(registered)
        else:
            allowed_ids = set(accessible_lines or [])
            for r in roles:
                if r.startswith("bp:"):
                    allowed_ids.add(r[3:])
        by_line: dict[str, list[str]] = {
            lid: qs
            for lid, qs in all_sug.items()
            if lid in registered and lid in allowed_ids
        }
        return CopilotSuggestions(by_line=by_line, common=COMMON_SUGGESTIONS)

    def system_prompt_with_user(self, api_base: str | None = None) -> str:
        """Render the system prompt + append a "current user" block.

        Used by real LLM backends so the model knows who is asking and
        what they can access.
        """
        from .llm.prompts import render_system_prompt
        base = render_system_prompt(api_base=api_base)
        if not self._active_user:
            return base
        user = self._active_user
        roles = user.get("roles") or []
        lines = user.get("accessible_lines") or []
        try:
            import json as _json
            user_block = _json.dumps(
                {
                    "username": user.get("username"),
                    "display_name": user.get("display_name"),
                    "roles": roles,
                    "accessible_lines": lines,
                },
                ensure_ascii=False,
            )
        except Exception:  # noqa: BLE001
            user_block = f"{user.get('username')} (roles={roles}, lines={lines})"
        return (
            base
            + "\n\n【当前用户身份(RBAC 上下文)】\n"
            + user_block
            + "\n注:你只能引用当前用户有访问权限的业务线的数据;若用户问题涉及无权限业务线,应明确告知。\n"
        )

    # ── Internals ──────────────────────────────────────────────────────

    def _build_response_from_mock(
        self,
        *,
        question: str,
        mock: Any,  # MockAnswer, but we type loosely
        parsed: dict[str, Any],
        request_line: str | None,
        backend_name: str | None = None,
    ) -> CopilotResponse:
        citations: list[Citation] = []
        for c in (mock.citations or []):
            try:
                citations.append(Citation(**c))
            except Exception as exc:  # noqa: BLE001
                logger.debug("skipping bad citation: %s (%s)", c, exc)
        return CopilotResponse(
            question=question,
            answer=mock.answer,
            citations=citations,
            chart_data=mock.chart_data,
            intent=mock.intent,
            confidence=float(mock.confidence),
            backend=backend_name or self._backend.name,
            line_id=parsed.get("line") or request_line,
            used_fallback=False,
            model=None,
            debug={
                "parsed": parsed,
                "request_line": request_line,
                **(mock.debug or {}),
            },
        )

    async def _ask_real_llm_async(
        self,
        req: CopilotRequest,
        parsed: dict[str, Any],
        effective_question: str,
        backend_override: Any | None = None,
    ) -> CopilotResponse:
        """Async path for real LLM backends (DeepSeek, Ollama) and FallbackBackend."""
        backend = backend_override or self._backend
        prompt = self._build_prompt(req, parsed, effective_question)
        # If we know who is asking, augment the system prompt with the
        # user's identity + roles so the LLM can refuse to leak data
        # the user can't see.
        sys_prompt: str | None = None
        if self._active_user is not None:
            try:
                sys_prompt = self.system_prompt_with_user()
            except Exception:  # noqa: BLE001
                sys_prompt = None
        try:
            raw = await backend.complete(
                prompt, max_tokens=1024, system_prompt=sys_prompt
            )
        except Exception as exc:  # noqa: BLE001 — defensive; FallbackBackend shouldn't raise
            raw = f"[LLM 后端调用失败: {exc}]"

        # Was this a FallbackBackend? If so, it may have a structured
        # MockAnswer in `last_answer` that we can use for citations + chart.
        citations: list[Citation] = []
        chart_data: dict[str, Any] | None = None
        used_fallback = False
        fallback_reason: str | None = None
        if isinstance(backend, FallbackBackend):
            used_fallback = backend.used_fallback
            fallback_reason = backend.last_error
            if used_fallback and backend.last_answer is not None:
                mock = backend.last_answer
                chart_data = mock.chart_data
                for c in (mock.citations or []):
                    try:
                        citations.append(Citation(**c))
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("skipping fallback citation: %s (%s)", c, exc)
        # Real LLMs: attach a generic citation pointing at the registry
        # so the user has a place to click (unless we already have richer
        # ones from a fallback).
        if not citations:
            citations = [
                Citation(
                    source="business_lines/registry.yaml",
                    title="业务线清单",
                    snippet="点击查看所有业务线",
                    url="/dashboard",
                )
            ]
        # Pull model name from primary if available.
        primary = (
            backend.primary
            if isinstance(backend, FallbackBackend)
            else backend
        )
        model = getattr(primary, "model", None)
        return CopilotResponse(
            question=req.question.strip(),
            answer=raw,
            citations=citations,
            chart_data=chart_data,
            intent=parsed.get("intent", "fallback_unknown"),
            confidence=max(0.5, float(parsed.get("confidence") or 0.5)),
            backend=backend.name,
            line_id=parsed.get("line") or req.line_id,
            used_fallback=used_fallback,
            fallback_reason=fallback_reason,
            model=model,
            debug={"parsed": parsed, "prompt_chars": len(prompt)},
        )

    def _ask_real_llm(
        self,
        req: CopilotRequest,
        parsed: dict[str, Any],
        effective_question: str,
        backend_override: Any | None = None,
    ) -> CopilotResponse:
        """Sync wrapper for real LLMs. We use asyncio to drive the async
        backend.complete() — but only if there is an event loop available.
        In a sync FastAPI handler we can use anyio.from_thread.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            return asyncio.run(
                self._ask_real_llm_async(
                    req, parsed, effective_question, backend_override
                )
            )
        # If we're already inside an event loop (rare for FastAPI sync
        # endpoints, but possible in tests), use the loop directly.
        return loop.run_until_complete(
            self._ask_real_llm_async(
                req, parsed, effective_question, backend_override
            )
        )

    def _build_prompt(
        self, req: CopilotRequest, parsed: dict[str, Any], effective_question: str
    ) -> str:
        """Build the *user-side* prompt for real LLMs.

        System prompt is added by the backend itself; here we just
        construct the user message using the prompts.build_prompt()
        factory. The mock_helpers data is NOT fetched here — that's the
        mock backend's job. The real LLM is given the question and the
        line context, and is expected to know how to interpret the
        endpoint catalog baked into its system prompt.
        """
        return build_prompt(
            question=effective_question,
            line_id=req.line_id,
            context_data=None,  # real LLM is self-sufficient; mock_helpers is mock-only
        )

    @staticmethod
    def _maybe_inject_line(question: str, line_id: str | None) -> str:
        """If a line_id is given and the question doesn't already mention
        it, prepend the line name so the parser picks it up.
        """
        if not line_id:
            return question
        if re.search(re.escape(line_id), question, re.IGNORECASE):
            return question
        return f"{line_id} {question}"
