"""
apps/api/app/routers/ai_models.py

Admin endpoints for the runtime-toggleable LLM provider registry.

Routes
------
GET    /api/ai-models                  — list all registered models
POST   /api/ai-models                  — admin: create a new model config
PATCH  /api/ai-models/{id}             — admin: update any field
DELETE /api/ai-models/{id}             — admin: soft-delete (is_active=False)
POST   /api/ai-models/{id}/test        — admin: trigger a smoke test
POST   /api/ai-models/{id}/set-default — admin: mark this model as default

All routes require the ``admin`` role (via ``require_admin_dep``).
The factory in ``app.services.llm.factory`` reads the table on every
call, so a POST/PATCH here takes effect on the next LLM request —
no service restart required.

Test endpoint contract
----------------------
The ``/test`` endpoint runs a short prompt through the configured
provider and returns:
  * ``ok``         — True iff the provider returned a non-empty answer
  * ``status``     — "ok" / "error"
  * ``latency_ms`` — wall-clock time measured at the API layer
  * ``sample_response`` — short snippet of the answer
  * ``error``      — error message when ``ok`` is False

The endpoint ALWAYS records the result in the ``last_tested_at`` /
``last_test_status`` / ``last_test_latency_ms`` /
``last_test_response`` columns of the row, regardless of outcome.
This gives the admin UI a "last seen alive" signal even when the
underlying provider is dead.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import text

from ..core.auth import CurrentUser
from ..core.logging import get_logger
from ..core.rbac import require_admin_dep
from ..core.secret import encrypt_secret, is_env_reference
from ..db.session import get_session_factory
from ..schemas.ai_models import (
    AIModelItem,
    AIModelListResponse,
    CreateAIModelRequest,
    TestAIModelRequest,
    TestAIModelResponse,
    UpdateAIModelRequest,
)
from ..services.llm.factory import (
    OpenAICompatibleBackend,
    _build_backend_for_row,
)
from ..services.llm.deepseek import DeepSeekBackend
from ..services.llm.mock import MockBackend
from ..services.llm.ollama import OllamaBackend

logger = get_logger(__name__)

router = APIRouter(prefix="/api/ai-models", tags=["ai-models"])


# ---------------------------------------------------------------------------
# Row <-> ORM mapping helpers
# ---------------------------------------------------------------------------


# Fields we always SELECT for the response. Keep this list in sync with
# the response schema (``AIModelItem``) so the test endpoint can reuse
# the same projection.
_ROW_FIELDS = (
    "id, name, provider, model_name, base_url, api_key, "
    "enabled, is_default, is_active, "
    "last_tested_at, last_test_status, last_test_latency_ms, "
    "last_test_response, created_at, updated_at"
)


def _to_item(m: Any) -> AIModelItem:
    """Build an ``AIModelItem`` from a SQLAlchemy row-mapping.

    The raw ``api_key`` ciphertext is intentionally NOT included in
    the response (it's a write-only secret). We expose two booleans
    instead: ``api_key_set`` (any non-empty value stored) and
    ``api_key_is_env_ref`` (``env:`` reference rather than a literal).
    """
    api_key_raw = m["api_key"]
    api_key_set = bool(api_key_raw) and str(api_key_raw).strip() != ""
    is_env_ref = is_env_reference(api_key_raw) if api_key_set else False
    # ISO-8601 string for timestamps so the JSON response is
    # serializable. ``None`` is preserved for unset columns.
    def _iso(v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)

    return AIModelItem(
        id=int(m["id"]),
        name=str(m["name"]),
        provider=str(m["provider"]),
        model_name=str(m["model_name"]),
        base_url=m["base_url"],
        api_key_set=api_key_set,
        api_key_is_env_ref=is_env_ref,
        enabled=bool(m["enabled"]),
        is_default=bool(m["is_default"]),
        is_active=bool(m["is_active"]),
        last_tested_at=_iso(m["last_tested_at"]),
        last_test_status=m["last_test_status"],
        last_test_latency_ms=m["last_test_latency_ms"],
        last_test_response=m["last_test_response"],
        created_at=_iso(m["created_at"]),
        updated_at=_iso(m["updated_at"]),
    )


async def _fetch_row(model_id: int) -> Optional[AIModelItem]:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(f"SELECT {_ROW_FIELDS} FROM ai_models WHERE id = :id"),
                {"id": model_id},
            )
        ).mappings().first()
    if not row:
        return None
    return _to_item(row)


# ---------------------------------------------------------------------------
# GET /api/ai-models  — list (admin only)
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=AIModelListResponse,
    summary="Admin: list all registered AI model configs",
)
async def list_models(
    user: CurrentUser = Depends(require_admin_dep),
) -> AIModelListResponse:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT {_ROW_FIELDS}
                    FROM ai_models
                    ORDER BY is_default DESC, id ASC
                    """
                )
            )
        ).mappings().all()
    items = [_to_item(r) for r in rows]
    return AIModelListResponse(count=len(items), models=items)


# ---------------------------------------------------------------------------
# POST /api/ai-models  — create (admin only)
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=AIModelItem,
    status_code=status.HTTP_201_CREATED,
    summary="Admin: create a new AI model config",
)
async def create_model(
    body: CreateAIModelRequest,
    user: CurrentUser = Depends(require_admin_dep),
) -> AIModelItem:
    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(
                text("SELECT id FROM ai_models WHERE name = :n"),
                {"n": body.name},
            )
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"ai_model name already exists: {body.name}",
            )

        encrypted_key: Optional[str] = None
        if body.api_key:
            # Both ``env:VAR`` references and literal values are stored
            # verbatim. The Fernet encryption only applies to literal
            # values; env references are resolved at call time.
            if is_env_reference(body.api_key):
                encrypted_key = body.api_key
            else:
                encrypted_key = encrypt_secret(body.api_key)

        new_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO ai_models
                        (name, provider, model_name, base_url, api_key,
                         enabled, is_default, is_active)
                    VALUES
                        (:name, :provider, :model_name, :base_url, :api_key,
                         :enabled, :is_default, TRUE)
                    RETURNING id
                    """
                ),
                {
                    "name": body.name,
                    "provider": body.provider,
                    "model_name": body.model_name,
                    "base_url": body.base_url,
                    "api_key": encrypted_key,
                    "enabled": bool(body.enabled),
                    "is_default": bool(body.is_default),
                },
            )
        ).scalar_one()

        if body.is_default:
            # Atomic: clear every other default then set this one.
            await session.execute(
                text("UPDATE ai_models SET is_default = FALSE WHERE id <> :id"),
                {"id": int(new_id)},
            )
            await session.execute(
                text("UPDATE ai_models SET is_default = TRUE WHERE id = :id"),
                {"id": int(new_id)},
            )
        await session.commit()

    item = await _fetch_row(int(new_id))
    assert item is not None
    logger.info(
        "create_model: admin=%s created id=%s name=%s provider=%s",
        user.username, new_id, body.name, body.provider,
    )
    return item


# ---------------------------------------------------------------------------
# PATCH /api/ai-models/{id}  — partial update
# ---------------------------------------------------------------------------


@router.patch(
    "/{model_id}",
    response_model=AIModelItem,
    summary="Admin: update a registered AI model (any field)",
)
async def update_model(
    model_id: int = Path(..., ge=1),
    body: UpdateAIModelRequest = ...,
    user: CurrentUser = Depends(require_admin_dep),
) -> AIModelItem:
    factory = get_session_factory()
    async with factory() as session:
        # Confirm the row exists. We use a separate SELECT (instead of
        # relying on rowcount) so the 404 carries a clear message and
        # doesn't race with the "last enabled" guard below.
        existing = (
            await session.execute(
                text("SELECT id, is_default FROM ai_models WHERE id = :id"),
                {"id": model_id},
            )
        ).mappings().first()
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ai_model not found: {model_id}",
            )

        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": model_id}
        if body.name is not None:
            # Duplicate-name guard: another row already has the same name?
            dupe = (
                await session.execute(
                    text(
                        "SELECT 1 FROM ai_models WHERE name = :n AND id <> :id"
                    ),
                    {"n": body.name, "id": model_id},
                )
            ).first()
            if dupe:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"ai_model name already exists: {body.name}",
                )
            set_clauses.append("name = :name")
            params["name"] = body.name
        if body.provider is not None:
            set_clauses.append("provider = :provider")
            params["provider"] = body.provider
        if body.model_name is not None:
            set_clauses.append("model_name = :model_name")
            params["model_name"] = body.model_name
        if body.base_url is not None:
            set_clauses.append("base_url = :base_url")
            params["base_url"] = body.base_url
        if body.api_key is not None:
            if body.api_key == "":
                # Explicit empty string → clear the key.
                set_clauses.append("api_key = NULL")
            elif is_env_reference(body.api_key):
                set_clauses.append("api_key = :api_key")
                params["api_key"] = body.api_key
            else:
                set_clauses.append("api_key = :api_key")
                params["api_key"] = encrypt_secret(body.api_key)
        if body.enabled is not None:
            # Last-enabled protection: refuse to disable the last
            # enabled+active row.
            if body.enabled is False:
                other_enabled = (
                    await session.execute(
                        text(
                            """
                            SELECT COUNT(*) FROM ai_models
                            WHERE enabled = TRUE AND is_active = TRUE
                              AND id <> :id
                            """
                        ),
                        {"id": model_id},
                    )
                ).scalar_one()
                if int(other_enabled) == 0:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="cannot disable the last enabled model",
                    )
            set_clauses.append("enabled = :enabled")
            params["enabled"] = bool(body.enabled)
        if body.is_active is not None:
            # Same last-enabled guard when the caller is soft-deleting.
            if body.is_active is False:
                other_enabled = (
                    await session.execute(
                        text(
                            """
                            SELECT COUNT(*) FROM ai_models
                            WHERE enabled = TRUE AND is_active = TRUE
                              AND id <> :id
                            """
                        ),
                        {"id": model_id},
                    )
                ).scalar_one()
                if int(other_enabled) == 0:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="cannot deactivate the last enabled model",
                    )
            set_clauses.append("is_active = :is_active")
            params["is_active"] = bool(body.is_active)

        if set_clauses:
            set_clauses.append("updated_at = NOW()")
            await session.execute(
                text(
                    f"UPDATE ai_models SET {', '.join(set_clauses)} WHERE id = :id"
                ),
                params,
            )

        if body.is_default is True:
            # Atomic: clear every other default then set this one.
            await session.execute(
                text("UPDATE ai_models SET is_default = FALSE WHERE id <> :id"),
                {"id": model_id},
            )
            await session.execute(
                text(
                    "UPDATE ai_models SET is_default = TRUE, "
                    "updated_at = NOW() WHERE id = :id"
                ),
                {"id": model_id},
            )
        elif body.is_default is False and bool(existing["is_default"]):
            # Demoting a default row. Re-promote the oldest enabled+
            # active row so the system is never without a default.
            replacement = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM ai_models
                        WHERE enabled = TRUE AND is_active = TRUE AND id <> :id
                        ORDER BY id ASC
                        LIMIT 1
                        """
                    ),
                    {"id": model_id},
                )
            ).first()
            if replacement:
                await session.execute(
                    text("UPDATE ai_models SET is_default = FALSE WHERE id = :id"),
                    {"id": model_id},
                )
                await session.execute(
                    text("UPDATE ai_models SET is_default = TRUE WHERE id = :id"),
                    {"id": int(replacement[0])},
                )
            else:
                # No replacement — keep this one as default and warn.
                # Better than leaving the system with no default.
                logger.warning(
                    "update_model: no replacement default available; "
                    "keeping id=%s as default", model_id,
                )
        await session.commit()

    item = await _fetch_row(model_id)
    assert item is not None
    logger.info("update_model: admin=%s updated id=%s", user.username, model_id)
    return item


# ---------------------------------------------------------------------------
# DELETE /api/ai-models/{id}  — soft delete
# ---------------------------------------------------------------------------


@router.delete(
    "/{model_id}",
    response_model=AIModelItem,
    summary="Admin: soft-delete a model (is_active=false). "
            "Refuses if it would leave the registry with zero enabled rows.",
)
async def soft_delete_model(
    model_id: int = Path(..., ge=1),
    user: CurrentUser = Depends(require_admin_dep),
) -> AIModelItem:
    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(
                text(
                    "SELECT id, is_default FROM ai_models WHERE id = :id"
                ),
                {"id": model_id},
            )
        ).mappings().first()
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ai_model not found: {model_id}",
            )
        other_enabled = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM ai_models
                    WHERE enabled = TRUE AND is_active = TRUE AND id <> :id
                    """
                ),
                {"id": model_id},
            )
        ).scalar_one()
        if int(other_enabled) == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cannot delete the last enabled model",
            )
        # Soft delete: flip is_active AND clear the default flag
        # (otherwise a soft-deleted row would still claim the slot).
        await session.execute(
            text(
                """
                UPDATE ai_models
                SET is_active = FALSE,
                    is_default = FALSE,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": model_id},
        )
        # If this row WAS the default, promote the next oldest enabled
        # row so the registry always has a default.
        if bool(existing["is_default"]):
            replacement = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM ai_models
                        WHERE enabled = TRUE AND is_active = TRUE
                        ORDER BY id ASC
                        LIMIT 1
                        """
                    )
                )
            ).first()
            if replacement:
                await session.execute(
                    text("UPDATE ai_models SET is_default = TRUE WHERE id = :id"),
                    {"id": int(replacement[0])},
                )
        await session.commit()
    item = await _fetch_row(model_id)
    assert item is not None
    logger.info(
        "soft_delete_model: admin=%s deactivated id=%s",
        user.username, model_id,
    )
    return item


# ---------------------------------------------------------------------------
# POST /api/ai-models/{id}/test  — smoke test
# ---------------------------------------------------------------------------


@router.post(
    "/{model_id}/test",
    response_model=TestAIModelResponse,
    summary="Admin: smoke-test a registered model. Records result on the row.",
)
async def test_model(
    model_id: int = Path(..., ge=1),
    body: TestAIModelRequest = TestAIModelRequest(),
    user: CurrentUser = Depends(require_admin_dep),
) -> TestAIModelResponse:
    """Run a short prompt through the model and record the result.

    The endpoint ALWAYS records the test outcome in the row's
    ``last_tested_at`` / ``last_test_status`` / ``last_test_latency_ms``
    / ``last_test_response`` columns, even on failure — so the admin
    UI can show "last seen alive: 5 min ago" for healthy rows and
    "last seen: HTTP 401" for misconfigured ones.
    """
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    f"SELECT {_ROW_FIELDS} FROM ai_models WHERE id = :id"
                ),
                {"id": model_id},
            )
        ).mappings().first()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ai_model not found: {model_id}",
            )

    from ..services.llm.factory import AIModelRow
    model_row = AIModelRow(
        id=int(row["id"]),
        name=str(row["name"]),
        provider=str(row["provider"]),
        model_name=str(row["model_name"]),
        base_url=row["base_url"],
        api_key=row["api_key"],
        enabled=bool(row["enabled"]),
        is_default=bool(row["is_default"]),
        is_active=bool(row["is_active"]),
    )
    try:
        backend = _build_backend_for_row(model_row)
    except Exception as exc:  # noqa: BLE001
        # Config error: the operator will see a clear message in the
        # response. Record the failure on the row.
        latency_ms = 0
        await _record_test_result(
            model_id=model_id,
            status_str="error",
            latency_ms=latency_ms,
            sample_response="",
            error=f"config: {exc}",
        )
        return TestAIModelResponse(
            ok=False,
            status="error",
            latency_ms=latency_ms,
            sample_response="",
            error=f"config: {exc}",
        )

    started = time.monotonic()
    err_msg: Optional[str] = None
    sample = ""
    ok = False
    try:
        # Backend.complete is async; run it in a fresh loop so this
        # endpoint stays sync (mirrors the pattern in
        # ``routers/copilot.py`` which intentionally avoids async +
        # sync-mock-helper deadlocks).
        import asyncio
        try:
            sample = asyncio.run(
                backend.complete(body.prompt, max_tokens=body.max_tokens)
            )
        except RuntimeError:
            # asyncio.run cannot be called from a running loop; fall
            # back to awaiting in the current loop. The endpoint
            # itself is `async def` so this branch is the normal path
            # under uvicorn.
            sample = await backend.complete(
                body.prompt, max_tokens=body.max_tokens
            )
        ok = bool(sample and str(sample).strip())
        if not ok:
            err_msg = "empty response"
    except Exception as exc:  # noqa: BLE001 — broad catch: test must always report
        err_msg = f"{type(exc).__name__}: {exc}"
        ok = False
    latency_ms = int((time.monotonic() - started) * 1000)
    status_str = "ok" if ok else "error"
    sample_snip = (sample or "")[:300] if ok else ""
    await _record_test_result(
        model_id=model_id,
        status_str=status_str,
        latency_ms=latency_ms,
        sample_response=sample_snip,
        error=err_msg,
    )
    return TestAIModelResponse(
        ok=ok,
        status=status_str,
        latency_ms=latency_ms,
        sample_response=sample_snip,
        error=err_msg,
    )


async def _record_test_result(
    *,
    model_id: int,
    status_str: str,
    latency_ms: int,
    sample_response: str,
    error: Optional[str],
) -> None:
    """Write the test result back to the row.

    Best-effort: a DB failure here is logged but does NOT propagate to
    the caller (the test endpoint's primary job is to return the
    smoke-test result, not to write to the row).
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    """
                    UPDATE ai_models
                    SET last_tested_at = NOW(),
                        last_test_status = :status,
                        last_test_latency_ms = :latency,
                        last_test_response = :resp,
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": model_id,
                    "status": status_str,
                    "latency": int(latency_ms),
                    # Combine the snippet with the error so the operator
                    # can see both at a glance. Cap at 1000 chars to
                    # avoid bloating the row.
                    "resp": (f"{sample_response}\n\n[error: {error}]" if error else sample_response)[:1000],
                },
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_record_test_result: failed to write test result for id=%s: %s",
            model_id, exc,
        )


# ---------------------------------------------------------------------------
# POST /api/ai-models/{id}/set-default  — explicit default promotion
# ---------------------------------------------------------------------------


@router.post(
    "/{model_id}/set-default",
    response_model=AIModelItem,
    summary="Admin: mark this model as the default. "
            "Atomically clears the default flag on every other row.",
)
async def set_default_model(
    model_id: int = Path(..., ge=1),
    user: CurrentUser = Depends(require_admin_dep),
) -> AIModelItem:
    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(
                text(
                    "SELECT id, enabled, is_active FROM ai_models WHERE id = :id"
                ),
                {"id": model_id},
            )
        ).mappings().first()
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ai_model not found: {model_id}",
            )
        if not (bool(existing["enabled"]) and bool(existing["is_active"])):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cannot set a disabled/inactive model as default",
            )
        await session.execute(text("UPDATE ai_models SET is_default = FALSE"))
        await session.execute(
            text(
                """
                UPDATE ai_models
                SET is_default = TRUE, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": model_id},
        )
        await session.commit()
    item = await _fetch_row(model_id)
    assert item is not None
    logger.info(
        "set_default_model: admin=%s set id=%s as default",
        user.username, model_id,
    )
    return item


__all__ = ["router"]
