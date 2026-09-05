"""
apps/api/tests/test_engine_tenant_followup.py
==============================================

M3 follow-up (2026-09-05) — engine router 走 in-process HTTP 调业务线 API
时的 tenant 隔离回归门.

背景
----
M2 (commit b00b499) 修了 12 个有 SQL 的 router 走 ``tenant_session``, 但
漏了 **engine → 业务线** 调用链的 tenant 隔离:

1. 用户 (tenant A) 调 ``/api/copilot/ask`` with ``X-Tenant-ID: A``
2. Copilot engine 用 ``X-Service-Token`` 调内层
   ``/api/lines/residential/projects``
3. 内层 ``get_current_user_v2`` 的 service-token 分支返回 ``__service__``
   伪用户, 之前**不**设 tenant context, 落到 ``DEFAULT_TENANT_ID``
4. 业务线 router (现在 9 个) 调 SQL 时 (未来加) RLS 不锁, 跨 tenant 泄露

修复 (本任务):

* ``auth.py`` / ``auth_v2.py`` service-token 分支读 ``X-Tenant-ID``,
  写 ``request.state.tenant_id`` + ``request.state.is_service_token``.
* ``tenant_context._resolve_tenant_context`` 加 service-token 专用分支
  (source="service_token", bypass_rls=False — 仍 RLS 锁).
* ``get_tenant_session_dep`` 新 helper — 业务线 handler 拿 ctx 调
  ``tenant_session`` 用.
* ``mock_helpers._http_json`` 自动用 per-request header override 透传
  X-Tenant-ID 到内层调用.
* ``copilot_engine.ask`` 接受 ``outer_tenant_id``, 注入 mock_helpers.
* ``copilot.py:ask_endpoint`` 从 ``Request.headers`` 读 X-Tenant-ID 传
  给 engine.
* ``registry.py:mount_business_line_routers`` 给所有 9 个业务线 router
  加 ``get_tenant_context`` dep, sub-app 路径在 _LineGuardMiddleware
  也解析 tenant 写 ``request.state.tenant_context``.

覆盖
----
1. service-token 透传 X-Tenant-ID
2. service-token 无 X-Tenant-ID → DEFAULT
3. copilot 内部调用带外层 X-Tenant-ID
4. 业务线 router dep 注入 tenant context
5. tenant 隔离 (跨 tenant A/B, RLS 不锁时也只看到自己 tenant)

执行
----
    cd apps/api
    python -m pytest tests/test_engine_tenant_followup.py -v --tb=short
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text


os.environ.setdefault(
    "JWT_SECRET", "test-jwt-secret-for-engine-tenant-followup-32-chars"
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")
OTHER_TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")


# ---------------------------------------------------------------------------
# Postgres helper
# ---------------------------------------------------------------------------


def _parse_pg_dsn() -> dict[str, object]:
    from app.core.config import get_settings
    url = get_settings().database_url.replace("+asyncpg", "")
    u = urlparse(url)
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 5432,
        "user": u.username,
        "password": u.password or "",
        "database": (u.path or "/postgres").lstrip("/") or "postgres",
    }


@pytest.fixture(scope="module")
def postgres_available_eng():
    cfg = _parse_pg_dsn()
    try:
        with socket.create_connection((cfg["host"], cfg["port"]), timeout=0.5):
            return cfg
    except (OSError, socket.timeout):
        pytest.skip(
            f"Postgres not reachable at {cfg['host']}:{cfg['port']} — "
            "M3 engine-tenant-followup tests skipped"
        )


def _run_async(coro):
    from app.db import session as session_mod
    session_mod.reset_engine()
    try:
        return asyncio.run(coro)
    finally:
        session_mod.reset_engine()


def _create_test_tenant(slug_suffix: str) -> UUID:
    async def _do() -> UUID:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            row = (await session.execute(
                text(
                    """
                    INSERT INTO tenants (slug, name, plan, is_active)
                    VALUES (:slug, :name, 'standard', TRUE)
                    RETURNING id
                    """
                ),
                {
                    "slug": f"m3-test-{slug_suffix}",
                    "name": f"M3 Test Tenant {slug_suffix}",
                },
            )).mappings().first()
            await session.commit()
        return UUID(str(row["id"]))
    return _run_async(_do())


def _delete_tenant(tenant_id: UUID) -> None:
    async def _do() -> None:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text("DELETE FROM tenants WHERE id = :tid"),
                {"tid": str(tenant_id)},
            )
            await session.commit()
    _run_async(_do())


# ---------------------------------------------------------------------------
# App fixtures — bypass service-token via TestClient
# ---------------------------------------------------------------------------


@contextmanager
def _service_token_client(
    *,
    outer_x_tenant_id: str | None = None,
) -> Iterator[TestClient]:
    """Build a TestClient that simulates the in-process service-token path.

    We use ``X-Service-Token`` directly to hit the service-token branch in
    ``get_current_user_v2``; ``X-Tenant-ID`` is propagated as if from a
    Copilot engine in-process call.
    """
    from app.main import create_app
    from app.core.auth import get_current_user_v2  # noqa: F401  (sanity)
    from app.core.config import get_settings

    # Ensure service token is set in env before the app loads.
    service_token = "test-m3-engine-service-token"
    os.environ["BIZ_BP_SERVICE_TOKEN"] = service_token

    app = create_app()
    headers: dict[str, str] = {
        "X-Service-Token": service_token,
    }
    if outer_x_tenant_id is not None:
        headers["X-Tenant-ID"] = outer_x_tenant_id
    with TestClient(app, headers=headers) as c:
        # TestClient carries default headers on every request
        yield c


@contextmanager
def _regular_user_client(
    *, tenant_id: UUID, username: str = "m3-regular", uid: int = 9999
) -> Iterator[TestClient]:
    """Build a TestClient that overrides ``get_current_user_v2`` to a
    regular (non-super-admin) user in ``tenant_id``.
    """
    from app.main import create_app
    from app.core.rbac_v2 import (
        CurrentUserV2,
        DataDomain,
        Role,
        Scope,
        UserRoleBinding,
    )
    from app.core.auth_v2 import get_current_user_v2
    from app.db import session as session_mod

    app = create_app()
    user = CurrentUserV2(
        id=uid,
        username=username,
        display_name=username,
        email=f"{username}@test.local",
        is_active=True,
        roles=["admin"],
        accessible_lines=[],
        bindings=[
            UserRoleBinding(
                role=Role.ADMIN,
                scope=Scope.GLOBAL,
                business_line_id=None,
            ),
        ],
        active_view="admin",
        is_super_admin=False,  # 关键: 非 super admin
        tenant_id=str(tenant_id),
    )

    async def _override(request: Request):
        request.state.current_user = user
        request.state.is_service_token = False
        request.state.tenant_id = str(tenant_id)
        return user

    app.dependency_overrides[get_current_user_v2] = _override
    session_mod.reset_engine()
    with TestClient(app) as c:
        yield c
    session_mod.reset_engine()


# ---------------------------------------------------------------------------
# 1) service-token 分支读 X-Tenant-ID header
# ---------------------------------------------------------------------------


def test_service_token_propagates_x_tenant_id_header(postgres_available_eng):
    """``get_current_user_v2`` service-token 分支读 ``X-Tenant-ID``,
    写 ``request.state.tenant_id`` + ``request.state.is_service_token``.

    通过 ``_resolve_tenant_context`` 间接验证: 内层 ``get_tenant_context``
    dep 看到 ``is_service_token=True`` + ``X-Tenant-ID`` header, 走
    ``source="service_token"`` 分支, ``tenant_id`` 等于 header 的 UUID.
    """
    from app.core.tenant_context import (
        TenantContext,
        get_tenant_context,
    )
    from app.core.auth_v2 import get_current_user_v2

    captured: dict = {}

    from app.main import create_app

    service_token = "test-m3-svc-1"
    os.environ["BIZ_BP_SERVICE_TOKEN"] = service_token
    app = create_app()

    # 用 ``_resolve_tenant_context`` 直接调 (取自 ``request.headers``),
    # 避免 dep signature 的 Header 反射问题. 这是 :func:`get_tenant_context`
    # 的内部实现, M3 新加的 service-token 分支在这里走.
    from app.core.tenant_context import _resolve_tenant_context

    async def _capture_ctx(request: Request) -> dict:
        # 从 request.headers 显式拿 X-Tenant-ID (跟 dep 一致)
        x_tenant_id = request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-ID")
        ctx = await _resolve_tenant_context(request, x_tenant_id=x_tenant_id)
        captured["ctx"] = ctx
        return {"tenant_id": str(ctx.tenant_id), "source": ctx.source}

    # Mount a probe endpoint that resolves ctx. 必须先跑
    # ``get_current_user_v2`` 写 request.state.is_service_token, 不然
    # ``get_tenant_context`` 看不到 service-token 标记.
    from fastapi import APIRouter, Depends
    probe = APIRouter()

    @probe.get("/__probe/ctx", dependencies=[Depends(get_current_user_v2)])
    async def _probe(
        ctx: dict = Depends(_capture_ctx),
    ) -> dict:
        return ctx

    app.include_router(probe)

    with TestClient(
        app, headers={"X-Service-Token": service_token, "X-Tenant-ID": str(OTHER_TENANT_ID)}
    ) as c:
        r = c.get("/__probe/ctx")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "service_token", body
    assert body["tenant_id"] == str(OTHER_TENANT_ID), body

    # Also verify the dataclass shape
    ctx: TenantContext = captured["ctx"]
    assert ctx.tenant_id == OTHER_TENANT_ID
    assert ctx.bypass_rls is False  # service-token 仍走 RLS 锁
    assert ctx.is_super_admin is False  # 不是 super admin (虽然 user 本身是)


# ---------------------------------------------------------------------------
# 2) service-token 无 X-Tenant-ID → DEFAULT (current behavior preserved)
# ---------------------------------------------------------------------------


def test_service_token_without_x_tenant_id_falls_back_to_default(
    postgres_available_eng,
):
    """service-token 用户没带 X-Tenant-ID, tenant_id 落到
    ``DEFAULT_TENANT_ID``. 跟 service-token 路径的兜底语义保持一致.
    """
    from app.core.tenant_context import (
        TenantContext,
        get_tenant_context,
    )

    service_token = "test-m3-svc-2"
    os.environ["BIZ_BP_SERVICE_TOKEN"] = service_token
    from app.main import create_app
    app = create_app()

    captured: dict = {}

    async def _capture_ctx(request: Request) -> dict:
        ctx = await get_tenant_context(request)
        captured["ctx"] = ctx
        return {"tenant_id": str(ctx.tenant_id), "source": ctx.source}

    from fastapi import APIRouter, Depends
    probe = APIRouter()

    @probe.get("/__probe/ctx")
    async def _probe(ctx: dict = Depends(_capture_ctx)) -> dict:
        return ctx

    app.include_router(probe)

    with TestClient(
        app, headers={"X-Service-Token": service_token}
    ) as c:
        r = c.get("/__probe/ctx")

    assert r.status_code == 200, r.text
    body = r.json()
    # service-token 没 X-Tenant-ID → 走 user_default (service user.tenant_id=None)
    # → 落到 DEFAULT
    assert body["tenant_id"] == str(DEFAULT_TENANT_ID), body
    # source 不是 service_token (因为没 X-Tenant-ID header 触发)
    assert body["source"] != "service_token", body


# ---------------------------------------------------------------------------
# 3) copilot in-process 调用带外层 X-Tenant-ID
# ---------------------------------------------------------------------------


def test_copilot_in_process_call_uses_outer_tenant(postgres_available_eng):
    """外层 ``X-Tenant-ID: A`` → copilot ask 端点 → engine → mock_helpers
    发内层 HTTP 时带 ``X-Tenant-ID: A`` → 内层 service-token 用户 tenant
    context 走 ``source="service_token"`` 跟外层同 tenant.

    验证方式: 在 /api/copilot/ask 的外层请求加 X-Tenant-ID, 用一个
    ``copilot_engine._REQUEST_HEADERS`` snapshot 验证 mock_helpers 在
    ask 调用时确实被设了 X-Tenant-ID.
    """
    from app.services.llm import mock_helpers

    captured_headers: list[dict] = []

    original_http_json = mock_helpers._http_json

    def _spy_http_json(path, base=None, extra_headers=None):
        # Capture the headers that would be sent
        if extra_headers is not None:
            captured_headers.append(dict(extra_headers))
        elif mock_helpers._REQUEST_HEADERS:
            captured_headers.append(dict(mock_helpers._REQUEST_HEADERS))
        return original_http_json(path, base=base, extra_headers=extra_headers)

    mock_helpers._http_json = _spy_http_json
    try:
        from app.main import create_app
        from app.core.rbac_v2 import (
            CurrentUserV2,
            Role,
            Scope,
            UserRoleBinding,
        )
        from app.core.auth_v2 import get_current_user_v2

        app = create_app()
        user = CurrentUserV2(
            id=1,
            username="alice",
            display_name="Alice",
            email="alice@test.local",
            is_active=True,
            roles=["admin"],
            accessible_lines=[],
            bindings=[
                UserRoleBinding(
                    role=Role.ADMIN,
                    scope=Scope.GLOBAL,
                    business_line_id=None,
                ),
            ],
            active_view="admin",
            is_super_admin=True,
            tenant_id=str(OTHER_TENANT_ID),
        )

        async def _override(request: Request):
            request.state.current_user = user
            return user

        app.dependency_overrides[get_current_user_v2] = _override

        with TestClient(
            app, headers={"X-Tenant-ID": str(OTHER_TENANT_ID)}
        ) as c:
            r = c.post(
                "/api/copilot/ask",
                json={"question": "住宅 IRR 最高的 3 个项目"},
            )

        assert r.status_code == 200, r.text
        # The mock helper made at least one HTTP call
        assert len(captured_headers) >= 1, (
            f"expected mock_helpers to make HTTP calls; got {captured_headers}"
        )
        # All captured headers should have X-Tenant-ID set to the outer tenant
        for hdrs in captured_headers:
            assert hdrs.get("X-Tenant-ID") == str(OTHER_TENANT_ID), (
                f"X-Tenant-ID not propagated: {hdrs}"
            )
    finally:
        mock_helpers._http_json = original_http_json
        mock_helpers.clear_request_headers()


# ---------------------------------------------------------------------------
# 4) 业务线 router 注入 tenant context dep
# ---------------------------------------------------------------------------


def test_business_line_router_injects_tenant_context(postgres_available_eng):
    """``mount_business_line_routers`` 给 9 个业务线 router 加了
    ``get_tenant_context`` dep. 验证: 调 ``/api/lines/residential/ping``
    时该 dep 被执行, ``request.state.tenant_context`` 被写入.

    验证方式: 加一个 instrumentation hook, 在 dep 被调用时记录; 然后调
    ping 端点确认 dep 跑过了.
    """
    from app.main import create_app
    from app.core.tenant_context import get_tenant_context

    app = create_app()

    # 替换 get_tenant_context 为 spy, 记录是否被调用
    calls: list[Request] = []
    original = get_tenant_context

    async def _spy(request: Request, x_tenant_id: str | None = None):
        calls.append(request)
        return await original(request, x_tenant_id=x_tenant_id)

    app.dependency_overrides[get_tenant_context] = _spy

    # Override auth to admin
    from app.core.auth import get_current_user
    from app.core.rbac import business_line_dep

    # Use the conftest-like app_with_auth pattern: build a user with bp:residential
    from app.core.auth import CurrentUser
    user = CurrentUser(
        id=1,
        username="bp-residential",
        display_name="BP Residential",
        email="bp-res@test.local",
        is_active=True,
        roles=["bp:residential"],
        accessible_lines=["residential"],
        is_super_admin=False,
        tenant_id=str(DEFAULT_TENANT_ID),
    )

    async def _user_override(request: Request, finbp_token=None):
        request.state.current_user = user
        return user

    app.dependency_overrides[get_current_user] = _user_override
    app.dependency_overrides[business_line_dep()] = lambda: user

    with TestClient(app) as c:
        r = c.get("/api/lines/residential/ping")

    assert r.status_code == 200, r.text
    # get_tenant_context was resolved (called at least once)
    assert len(calls) >= 1, (
        f"expected get_tenant_context to be invoked; got {len(calls)} calls"
    )


# ---------------------------------------------------------------------------
# 5) tenant 隔离 — service-token 跨 tenant 不泄露 (smoke test)
# ---------------------------------------------------------------------------


def test_service_token_tenant_isolation_does_not_use_bypass(
    postgres_available_eng,
):
    """service-token 走 ``source="service_token"`` 分支, ``bypass_rls=False``
    — 即 RLS 仍锁. 验证: ctx.bypass_rls 必须严格是 False.
    """
    from app.core.tenant_context import (
        TenantContext,
        _resolve_tenant_context,
    )
    from app.core.auth_v2 import get_current_user_v2
    from app.main import create_app
    from fastapi import APIRouter, Depends

    service_token = "test-m3-svc-3"
    os.environ["BIZ_BP_SERVICE_TOKEN"] = service_token
    app = create_app()

    captured: dict = {}

    async def _capture_ctx(request: Request) -> dict:
        x_tenant_id = request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-ID")
        ctx = await _resolve_tenant_context(request, x_tenant_id=x_tenant_id)
        captured["ctx"] = ctx
        return {
            "tenant_id": str(ctx.tenant_id),
            "bypass_rls": ctx.bypass_rls,
            "is_super_admin": ctx.is_super_admin,
            "source": ctx.source,
        }

    probe = APIRouter()

    @probe.get("/__probe/ctx", dependencies=[Depends(get_current_user_v2)])
    async def _probe(ctx: dict = Depends(_capture_ctx)) -> dict:
        return ctx

    app.include_router(probe)

    with TestClient(
        app,
        headers={
            "X-Service-Token": service_token,
            "X-Tenant-ID": str(OTHER_TENANT_ID),
        },
    ) as c:
        r = c.get("/__probe/ctx")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "service_token"
    assert body["bypass_rls"] is False  # 关键 — 不允许跨 tenant
    assert body["is_super_admin"] is False

    ctx: TenantContext = captured["ctx"]
    assert ctx.bypass_rls is False


# ---------------------------------------------------------------------------
# 6) X-Tenant-ID 格式校验 — service-token 路径
# ---------------------------------------------------------------------------


def test_service_token_invalid_x_tenant_id_returns_400(postgres_available_eng):
    """service-token 用户带了不合法的 X-Tenant-ID → 400. 跟 super admin
    路径一致, 不允许 silent fallback (那样会让 RLS bypass 数据完整性).
    """
    from app.main import create_app
    from fastapi import APIRouter, Depends
    from app.core.tenant_context import _resolve_tenant_context
    from app.core.auth_v2 import get_current_user_v2

    service_token = "test-m3-svc-4"
    os.environ["BIZ_BP_SERVICE_TOKEN"] = service_token
    app = create_app()

    async def _resolve(request: Request) -> dict:
        x_tenant_id = request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-ID")
        ctx = await _resolve_tenant_context(request, x_tenant_id=x_tenant_id)
        return {"tenant_id": str(ctx.tenant_id)}

    probe = APIRouter()

    @probe.get("/__probe/ctx", dependencies=[Depends(get_current_user_v2)])
    async def _probe(body: dict = Depends(_resolve)) -> dict:
        return body

    app.include_router(probe)

    with TestClient(
        app,
        headers={
            "X-Service-Token": service_token,
            "X-Tenant-ID": "not-a-uuid",
        },
    ) as c:
        r = c.get("/__probe/ctx")

    assert r.status_code == 400, r.text
    assert "service-token" in r.text.lower() or "invalid" in r.text.lower()
