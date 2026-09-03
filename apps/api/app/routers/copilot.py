"""
apps/api/app/routers/copilot.py

AI Copilot HTTP endpoints.

Mounted at /api/copilot by `app.main` (NOT via the business-line
auto-discovery path, since the Copilot is cross-cutting).

Endpoints:
  POST /api/copilot/ask         — ask one question, get answer + citations
                                   (auth required; the active user +
                                    roles are folded into the system
                                    prompt so the LLM knows who is
                                    asking)
  GET  /api/copilot/suggestions — recommended starter questions
                                   (auth required; filtered to
                                   accessible lines)
  GET  /api/copilot/health      — current LLM backend name + registered
                                   lines (auth required)

The engine (`app.services.copilot_engine`) does the heavy lifting.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core.auth import CurrentUser, get_current_user
from ..core.logging import get_logger
from ..services.copilot_engine import (
    CopilotEngine,
    CopilotHealth,
    CopilotRequest,
    CopilotResponse,
    CopilotSuggestions,
    validate_question,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


# ─────────────────────────────────────────────────────────────────────────
# Ask
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/ask",
    response_model=CopilotResponse,
    summary="Ask a free-form finance question, get an answer with citations",
)
def ask_endpoint(
    req: CopilotRequest,
    user: CurrentUser = Depends(get_current_user),
) -> CopilotResponse:
    # NOTE: deliberately NOT `async def` — the mock helper makes sync
    # HTTP calls to the same API process (e.g. GET /api/lines/.../projects).
    # If the handler were async, the sync urllib call would block the
    # event loop, and the re-entrant request would deadlock. FastAPI
    # automatically runs sync handlers in a thread pool, which frees
    # the event loop to process the helper's HTTP call.
    err = validate_question(req.question)
    if err:
        # 400 for bad input — distinguish from internal errors.
        raise HTTPException(status_code=400, detail=err)
    # RBAC: if the request carries an explicit line_id, the user must
    # have access to that line. Otherwise the engine can fall back to
    # the user's accessible lines.
    if req.line_id:
        from ..core.rbac import require_business_line as _rbl
        # We can't await in a sync handler; run the coroutine in the
        # current thread via asyncio. FastAPI runs sync handlers in a
        # worker thread, so there's no event loop here — use a tiny
        # event-loop helper.
        import asyncio
        try:
            asyncio.run(_rbl(req.line_id, user))
        except HTTPException:
            raise
    engine = CopilotEngine()
    try:
        # Inject the active user into the engine so the system prompt
        # can mention "current user: alice (roles: admin)".
        try:
            engine.set_active_user(user.to_public_dict())
        except AttributeError:
            pass
        return engine.ask(req)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — never let the engine crash the API
        logger.exception("copilot.ask failed: %s", exc)
        # Return a fallback response, not a 500, so the UI can show it.
        return CopilotResponse(
            question=req.question.strip(),
            answer=f"[Copilot 后端异常: {exc}]",
            citations=[],
            chart_data=None,
            intent="error",
            confidence=0.0,
            backend="error",
            debug={"error": str(exc)},
        )


# ─────────────────────────────────────────────────────────────────────────
# Suggestions
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/suggestions",
    response_model=CopilotSuggestions,
    summary="Get recommended starter questions, by line and cross-line",
)
async def suggestions_endpoint(
    user: CurrentUser = Depends(get_current_user),
) -> CopilotSuggestions:
    engine = CopilotEngine()
    try:
        return engine.suggestions_for_user(
            roles=user.roles, accessible_lines=user.accessible_lines
        )
    except AttributeError:
        # Backward compat if the engine doesn't expose suggestions_for_user
        return engine.suggestions()


# ─────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=CopilotHealth,
    summary="Report the active LLM backend and registered lines",
)
async def health_endpoint(
    user: CurrentUser = Depends(get_current_user),
) -> CopilotHealth:
    engine = CopilotEngine()
    return engine.health()
