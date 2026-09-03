"""
apps/api/app/routers/copilot.py

AI Copilot HTTP endpoints.

Mounted at /api/copilot by `app.main` (NOT via the business-line
auto-discovery path, since the Copilot is cross-cutting).

Endpoints:
  POST /api/copilot/ask         — ask one question, get answer + citations
  GET  /api/copilot/suggestions — recommended starter questions
  GET  /api/copilot/health      — current LLM backend name + registered lines

The engine (`app.services.copilot_engine`) does the heavy lifting.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

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
def ask_endpoint(req: CopilotRequest) -> CopilotResponse:
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
    engine = CopilotEngine()
    try:
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
async def suggestions_endpoint() -> CopilotSuggestions:
    engine = CopilotEngine()
    return engine.suggestions()


# ─────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=CopilotHealth,
    summary="Report the active LLM backend and registered lines",
)
async def health_endpoint() -> CopilotHealth:
    engine = CopilotEngine()
    return engine.health()
