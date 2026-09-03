"""
apps/api/app/schemas/ai_models.py

Pydantic v2 models for the ``/api/ai-models`` admin endpoints.

The schema mirrors the ``ai_models`` table 1-to-1, with the addition
of validation on the provider enum, name length, and the optional
``is_default`` flag (only one row can be the default at a time — the
API enforces that at write time).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# Open set of provider strings. The CHECK constraint in
# ``ai_models_provider_check`` is the source of truth; this Literal
# just gives Pydantic a chance to 422 the request before it hits the
# DB. Adding a new provider here also requires updating the
# ``ai_models`` CHECK constraint in ``db/bootstrap.py``.
ProviderName = Literal[
    "openai", "deepseek", "ollama", "mock", "anthropic", "custom"
]


def _is_known_provider(v: str) -> bool:
    """Defensive check used by the validators below. Mirrors the SQL
    CHECK so a 422 fires BEFORE the round trip to PostgreSQL.
    """
    return v in {"openai", "deepseek", "ollama", "mock", "anthropic", "custom"}


# ---------------------------------------------------------------------------
# Request payloads
# ---------------------------------------------------------------------------


class CreateAIModelRequest(BaseModel):
    """Body for ``POST /api/ai-models``."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Unique display name, e.g. 'DeepSeek-V3-Prod'.",
    )
    provider: ProviderName = Field(
        ...,
        description=(
            "One of: openai / deepseek / ollama / mock / anthropic / custom. "
            "``custom`` is an OpenAI-compatible endpoint with a custom base_url."
        ),
    )
    model_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description=(
            "Upstream model identifier, e.g. 'deepseek-chat', 'gpt-4o-mini', "
            "'qwen2.5:7b'."
        ),
    )
    base_url: Optional[str] = Field(
        default=None,
        max_length=512,
        description=(
            "Endpoint URL. Required for ollama/custom; optional for openai / "
            "deepseek (uses the vendor's default if omitted)."
        ),
    )
    api_key: Optional[str] = Field(
        default=None,
        max_length=512,
        description=(
            "API key, OR a reference of the form ``env:VAR_NAME`` to read "
            "from the environment at call time. Ignored for ``provider=mock``."
        ),
    )
    enabled: bool = Field(default=True, description="Default true.")
    is_default: bool = Field(
        default=False,
        description=(
            "If true, the new row is marked default and all other rows are "
            "cleared atomically. Otherwise the row is added without touching "
            "the current default."
        ),
    )

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("provider")
    @classmethod
    def _provider_known(cls, v: str) -> str:
        if not _is_known_provider(v):
            raise ValueError(
                f"provider must be one of openai / deepseek / ollama / mock / "
                f"anthropic / custom; got {v!r}"
            )
        return v


class UpdateAIModelRequest(BaseModel):
    """Body for ``PATCH /api/ai-models/{id}``.

    All fields are optional; only the ones supplied are updated. To
    change the default row, set ``is_default=true`` (the API will clear
    the flag on every other row atomically).
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    provider: Optional[ProviderName] = None
    model_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    base_url: Optional[str] = Field(default=None, max_length=512)
    api_key: Optional[str] = Field(default=None, max_length=512)
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v2 = v.strip()
        if not v2:
            raise ValueError("name must not be blank")
        return v2

    @field_validator("provider")
    @classmethod
    def _provider_known(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _is_known_provider(v):
            raise ValueError(
                f"provider must be one of openai / deepseek / ollama / mock / "
                f"anthropic / custom; got {v!r}"
            )
        return v


class TestAIModelRequest(BaseModel):
    """Body for ``POST /api/ai-models/{id}/test``.

    Currently a stub — the prompt defaults to "ping" but callers can
    override for a more meaningful smoke test.
    """

    prompt: str = Field(
        default="ping",
        max_length=512,
        description="Test prompt. Defaults to 'ping'.",
    )
    max_tokens: int = Field(default=16, ge=1, le=64)


# ---------------------------------------------------------------------------
# Response payloads
# ---------------------------------------------------------------------------


class AIModelItem(BaseModel):
    """One row of the ``ai_models`` table.

    The ``api_key`` field is INTENTIONALLY omitted from the response —
    it's a write-only secret. ``api_key_set`` is a boolean the UI can
    show to confirm a key is configured without leaking the value.
    """

    id: int
    name: str
    provider: str
    model_name: str
    base_url: Optional[str] = None
    api_key_set: bool = Field(
        default=False,
        description=(
            "True if a non-empty api_key is stored (after encryption). "
            "Always false when reading via the list endpoint — the actual "
            "ciphertext is never returned."
        ),
    )
    api_key_is_env_ref: bool = Field(
        default=False,
        description=(
            "True if the stored api_key is an ``env:VAR`` reference. The UI "
            "shows a different hint in that case so operators know the secret "
            "is read from the process environment at call time."
        ),
    )
    enabled: bool
    is_default: bool
    is_active: bool
    last_tested_at: Optional[str] = None
    last_test_status: Optional[str] = None
    last_test_latency_ms: Optional[int] = None
    last_test_response: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AIModelListResponse(BaseModel):
    count: int
    models: list[AIModelItem]


class TestAIModelResponse(BaseModel):
    """Result of ``POST /api/ai-models/{id}/test``.

    ``ok`` is True iff the provider returned a non-empty answer within
    the timeout. ``latency_ms`` is the wall-clock time measured at the
    API layer. ``sample_response`` is a short snippet of the answer
    (or the error message when ``ok`` is False).
    """

    ok: bool
    status: str = Field(
        description="One of 'ok' / 'error' — mirrors the ``last_test_status`` column."
    )
    latency_ms: int
    sample_response: str
    error: Optional[str] = None


__all__ = [
    "AIModelItem",
    "AIModelListResponse",
    "CreateAIModelRequest",
    "TestAIModelRequest",
    "TestAIModelResponse",
    "UpdateAIModelRequest",
]
