"""
apps/api/app/core/tenant_context.py
==================================

M2 起步 — 把"当前请求的 tenant"绑到 FastAPI request lifecycle 上.

设计
----
- tenant_id 来自 :http:header:`X-Tenant-ID` (或 query param ``?tenant=``).
- super admin (``is_super_admin=True`` in ``users`` table) 可显式切 tenant.
- 普通用户从 ``user.tenant_id`` 拿, 忽略 ``X-Tenant-ID``.
- M3 service-token (2026-09-05) 内部调用 (Copilot mock engine) 透传
  ``X-Tenant-ID`` header — 走专用 ``source="service_token"`` 路径, 解析
  service 头里带的 tenant.
- 没 tenant_id 的请求 → default tenant (:data:`DEFAULT_TENANT_ID`).
- ``bypass_rls`` 仅 super admin 可开. 内部 service-token 调用**不**开
  bypass_rls — 即透传 tenant 后, 内层 SQL 仍走 RLS 锁, 跟外层请求同
  tenant. 这正是 M3 修的"engine router tenant 泄露链"语义.

为什么需要这个 dep
------------------
M1 启用了 RLS (Row-Level Security) on 6 张业务表 + ``tenant_lock`` policy.
任何 SELECT / UPDATE / DELETE 必须先 :sql:`SET LOCAL app.tenant_id = '<uuid>'`
才能看到行. 单独让每个 router handler 自己算 tenant_id 既冗余又容易漏,
所以用一个 FastAPI dep 在请求进入时算一次, 然后 router 用
:func:`app.db.tenant.tenant_session` 包装 DB 调用.

用法
----
.. code-block:: python

    from fastapi import Depends
    from app.core.tenant_context import TenantContext, get_tenant_context
    from app.db.tenant import tenant_session

    @router.get("/...")
    async def handler(ctx: TenantContext = Depends(get_tenant_context)):
        async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
            rows = await session.execute(text("SELECT id FROM users"))

    # 业务线 router 用 get_tenant_session_dep 拿到 ctx, 再显式
    # ``async with tenant_session(ctx.tenant_id, ctx.bypass_rls)``:
    from app.core.tenant_context import get_tenant_session_dep

    async def my_handler(
        user: CurrentUser = Depends(get_current_user),
        ctx: TenantContext = Depends(get_tenant_session_dep),
    ):
        async with tenant_session(ctx.tenant_id, ctx.bypass_rls) as session:
            ...

请求 lifecycle 集成
-------------------
本 dep 读 :attr:`request.state.current_user` — 该 attribute 由
:func:`app.core.auth.get_current_user` (v1) 或
:func:`app.core.auth_v2.get_current_user_v2` (v2) 写入. 这两个 dep 必须
先于本 dep 执行. FastAPI 默认按参数声明顺序解析 dep, 所以把 ``user`` 参
数放在 ``ctx`` 之前即可:

.. code-block:: python

    async def handler(
        user: CurrentUser = Depends(get_current_user),
        ctx: TenantContext = Depends(get_tenant_context),
    ): ...
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status

from ..db.tenant import DEFAULT_TENANT_ID


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TenantContext:
    """Per-request tenant binding.

    Attributes
    ----------
    tenant_id:
        The tenant the current request should be scoped to. Always a
        valid :class:`uuid.UUID` (never ``None``) — falls back to
        :data:`DEFAULT_TENANT_ID` when no other source provides one.
    bypass_rls:
        When ``True``, the :func:`tenant_session` helper will set
        ``app.bypass_rls = 'on'``. This is a no-op in M2 (the M1 RLS
        policy doesn't consult it) but the call site is forward-compat
        with M3+ super-admin features.
    is_super_admin:
        True iff the current user is flagged as super admin
        (``users.is_super_admin = TRUE``). Super admins can switch
        tenant via :http:header:`X-Tenant-ID`; everyone else ignores
        the header.
    source:
        Where ``tenant_id`` came from. One of:
          - ``"header"``  — super admin provided :http:header:`X-Tenant-ID`
          - ``"service_token"`` — service-token 内部调用, header 透传
            (M3, 2026-09-05)
          - ``"user_default"`` — taken from ``user.tenant_id``
          - ``"default"`` — fallback (:data:`DEFAULT_TENANT_ID`)
        Useful for logging / debugging but not consulted by any
        business logic.
    """

    tenant_id: UUID
    bypass_rls: bool
    is_super_admin: bool
    source: str


# ---------------------------------------------------------------------------
# Core resolution
# ---------------------------------------------------------------------------


async def _resolve_tenant_context(
    request: Request,
    x_tenant_id: Optional[str],
) -> TenantContext:
    """Resolve the per-request :class:`TenantContext`.

    优先级
    ------
    0. service-token 内部调用 + ``X-Tenant-ID`` header (M3 2026-09-05) —
       走 source="service_token", bypass_rls=False (RLS 仍锁, 防跨 tenant).
    1. ``X-Tenant-ID`` header (super admin 显式切)
    2. 当前用户的 ``user.tenant_id`` (普通用户)
    3. :data:`DEFAULT_TENANT_ID` (兜底, 兼容未登录路径)

    Raises
    ------
    fastapi.HTTPException
        400 if :http:header:`X-Tenant-ID` is provided but unparseable.
    """
    user = getattr(request.state, "current_user", None)
    is_super = bool(user and getattr(user, "is_super_admin", False))
    is_service = bool(getattr(request.state, "is_service_token", False))

    # 0. service-token 内部调用 (M3, 2026-09-05) — 透传外层 tenant,
    #    不开 bypass_rls, 走 RLS tenant_lock.
    if is_service and x_tenant_id:
        try:
            tid = UUID(x_tenant_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid X-Tenant-ID (service-token): {x_tenant_id!r}",
            )
        return TenantContext(
            tenant_id=tid,
            bypass_rls=False,
            is_super_admin=False,
            source="service_token",
        )

    # 1. super admin + header
    if is_super and x_tenant_id:
        try:
            tid = UUID(x_tenant_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid X-Tenant-ID: {x_tenant_id!r}",
            )
        return TenantContext(
            tenant_id=tid,
            bypass_rls=True,
            is_super_admin=True,
            source="header",
        )

    # 2. 普通用户从 user.tenant_id
    if user is not None:
        user_tid = getattr(user, "tenant_id", None)
        if user_tid is not None:
            try:
                tid = UUID(str(user_tid))
            except (ValueError, TypeError):
                # 损坏的 tenant_id 静默回落到 default — 不可让单个坏行
                # 拖垮整条请求路径
                tid = DEFAULT_TENANT_ID
            return TenantContext(
                tenant_id=tid,
                bypass_rls=is_super,
                is_super_admin=is_super,
                source="user_default",
            )

    # 3. 兜底
    return TenantContext(
        tenant_id=DEFAULT_TENANT_ID,
        bypass_rls=is_super,
        is_super_admin=is_super,
        source="default",
    )


# ---------------------------------------------------------------------------
# FastAPI deps
# ---------------------------------------------------------------------------


async def get_tenant_context(
    request: Request,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
) -> TenantContext:
    """FastAPI dep — 解析当前请求的 tenant context.

    行为: 同 :func:`_resolve_tenant_context`. 单独抽出 dep 是为了 FastAPI
    依赖注入时能直接 ``Depends(get_tenant_context)`` 调用.
    """
    return await _resolve_tenant_context(request, x_tenant_id)


async def get_tenant_session_dep(
    request: Request,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
) -> TenantContext:
    """FastAPI dep — 业务线 router 用的 ``TenantContext`` 注入.

    与 :func:`get_tenant_context` 等价, 只是命名上明确"用于
    ``tenant_session`` 调用". 业务线 router 写在 :file:`registry.py` 的
    ``include_router(..., dependencies=[...])`` 时也用 :func:`get_tenant_context`
    (那是给 dep 列表用的); 业务线 handler 自身显式调 :func:`tenant_session`
    时用这个 dep 拿 ``ctx.tenant_id``.
    """
    return await _resolve_tenant_context(request, x_tenant_id)


__all__ = [
    "TenantContext",
    "get_tenant_context",
    "get_tenant_session_dep",
]
