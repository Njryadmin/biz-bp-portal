"""
apps/api/app/services/llm/base.py

Abstract LLM backend protocol.

The Copilot engine picks an LLM backend at runtime. The default mock backend
is a deterministic rule engine over the registered business-line APIs (no
external dependencies, no network). The DeepSeek and Ollama backends are
optional — the factory `get_llm_backend()` returns the mock if neither
DEEPSEEK_API_KEY nor OLLAMA_BASE_URL is set in the environment.

Backend contract:
    - `complete(prompt, *, max_tokens)` → free-form text
    - `embed(text)` → list[float] (optional; mock returns empty list)
    - `name` → str identifier ("mock" | "deepseek" | "ollama")

The base protocol is intentionally narrow. Higher-level concerns (tool
dispatch, citation extraction, prompt templating) live in
`apps/api/app/services/copilot_engine.py` and are shared across backends.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMBackend(Protocol):
    """Abstract LLM backend. Implementations may be sync or async.

    Note: The Copilot engine calls these via `await backend.complete(...)`,
    so concrete backends must define them as `async def`.
    """

    name: str

    async def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        """Return a completion for the given prompt.

        Implementations MUST be safe to call concurrently. A request must
        never raise for transient errors — instead return a friendly
        fallback string so the Copilot can wrap it in a citation.
        """
        ...

    async def embed(self, text: str) -> list[float]:
        """Return an embedding for `text`. The mock returns an empty list.

        Embeddings are not yet used by the Copilot engine, but the contract
        is here so a future RAG layer can plug in without changing the
        backend interface.
        """
        ...
