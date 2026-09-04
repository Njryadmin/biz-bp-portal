"""
apps/api/app/core/auth_v2.py

基于 v1 CurrentUser 的扩展:
    • UserRoleBinding 列表(支持 (role, scope, line_id) 三元组)
    • active_view 视角切换
    • can_access_domain(line_id, domain, write) 核心判断
    • active_perspective() 自动判定最强视角

DB 表结构变更见 infra/migrations/001_rbac_v2.sql
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import text

from .auth import _cookie_name, _load_user_by_id, decode_token
from .config import get_settings
from .logging import get_logger
from .rbac_v2 import (
    CurrentUserV2,
    DataDomain,
    Role,
    Scope,
    UserRoleBinding,
)
from ..db.session import get_session_factory

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# DB 加载(扩展 v1 的 _load_user_by_id,加载完整 binding 列表)
# ---------------------------------------------------------------------------


def _v1_role_to_bindings(role: str, accessible_lines: list[str]) -> list[UserRoleBinding]:
    """启发式:把 v1 的 role 字符串推断成 v2 UserRoleBinding 列表。

    用于 migration 001_rbac_v2.sql 还没跑、user_roles 表只有 (user_id, role)
    两列的场景。

    规则:
      • role ∈ {admin, auditor, viewer}    → 1 个 binding (role=对应, scope=global)
      • role LIKE 'bp:%'                    → 1 个 binding (role=line_owner,
                                               scope=business_line, line_id=后半段)
      • role = 'bp:my-line' (孤儿,无匹配业务线) → 跳过(对齐 bootstrap.py:241-258
                                                 清理模式)
    """
    if role in (Role.ADMIN.value, Role.AUDITOR.value, Role.VIEWER.value):
        return [
            UserRoleBinding(
                role=Role(role),
                scope=Scope.GLOBAL,
                business_line_id=None,
            )
        ]
    if role.startswith("bp:"):
        line_id = role[3:]
        # 孤儿检测:line_id 不在 user_business_lines 里的,丢弃
        if line_id and line_id not in (accessible_lines or []):
            logger.warning(
                "load_user_v2: dropping orphan role '%s' (line '%s' "
                "not in user_business_lines)",
                role,
                line_id,
            )
            return []
        return [
            UserRoleBinding(
                role=Role.LINE_OWNER,
                scope=Scope.BUSINESS_LINE,
                business_line_id=line_id or None,
            )
        ]
    # 未知 role (v2 才有的 fin_bp / hr_bp 等),无法从 v1 字符串推断,跳过
    return []


async def load_user_v2(user_id: int) -> CurrentUserV2 | None:
    """从 DB 加载完整 v2 CurrentUser。

    需要 user_roles 表新增 scope + line_id 字段(见 migration 001)。

    优雅降级:如果 user_roles 还没有 scope/line_id 列(migration 还没跑),
    回退到 v1 模式 — 只 SELECT role,然后用启发式推断 binding。8 个种子用户
    都不会掉线。
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            user_row = (
                await session.execute(
                    text(
                        # M2: 多读 is_super_admin + tenant_id (tenant_id 字段
                        # CurrentUserV2 还没有 — 用 getattr 回退, M3 再加).
                        "SELECT id, username, display_name, email, is_active, "
                        "is_super_admin, tenant_id "
                        "FROM users WHERE id = :uid"
                    ),
                    {"uid": user_id},
                )
            ).mappings().first()
            if not user_row or not user_row["is_active"]:
                return None

            # 业务线访问列表(从 user_business_lines 派生,保留 v1 兼容)
            lines_rows = (
                await session.execute(
                    text(
                        "SELECT line_id FROM user_business_lines "
                        "WHERE user_id = :uid ORDER BY line_id"
                    ),
                    {"uid": user_id},
                )
            ).scalars().all()
            accessible_lines = [str(x) for x in (lines_rows or [])]

            # v2 加载: role + scope + line_id
            try:
                bindings_rows = (
                    await session.execute(
                        text(
                            """
                            SELECT role, scope, line_id
                            FROM user_roles
                            WHERE user_id = :uid
                            ORDER BY role
                            """
                        ),
                        {"uid": user_id},
                    )
                ).mappings().all()
                used_v1_fallback = False
            except Exception as exc:  # noqa: BLE001
                # 捕获 UndefinedColumnError / psycopg2.errors.UndefinedColumn /
                # asyncpg.UndefinedColumnError — DB schema 还是 v1 的,
                # scope / line_id 列不存在。回退到 v1 启发式。
                logger.warning(
                    "load_user_v2: v2 columns missing (uid=%s, exc=%s); "
                    "falling back to v1 heuristic",
                    user_id,
                    exc,
                )
                v1_role_rows = (
                    await session.execute(
                        text(
                            "SELECT role FROM user_roles "
                            "WHERE user_id = :uid ORDER BY role"
                        ),
                        {"uid": user_id},
                    )
                ).scalars().all()
                bindings_rows = [
                    {"role": str(r), "scope": None, "line_id": None}
                    for r in (v1_role_rows or [])
                ]
                used_v1_fallback = True
    except Exception as exc:  # noqa: BLE001
        logger.error("load_user_v2 db error", exc_info=exc)
        return None

    bindings: list[UserRoleBinding] = []
    roles: list[str] = []
    for row in bindings_rows:
        # 启发式路径: row["scope"] is None (回退模式)
        if row.get("scope") is None:
            inferred = _v1_role_to_bindings(row["role"], accessible_lines)
            if not inferred:
                # 孤儿 bp:my-line — 跳过(不加入 roles / bindings)
                continue
            for b in inferred:
                if b not in bindings:
                    bindings.append(b)
                # v2 enum 名加入 roles,保证 has_role(Role.X) 可用;
                # 同时保留 v1 原始 role 字符串(向后兼容 v1 has_role("bp:..."))
                if b.role.value not in roles:
                    roles.append(b.role.value)
            if used_v1_fallback:
                raw = row["role"]
                if raw not in roles:
                    roles.append(raw)
            continue
        # v2 路径:完整 binding
        try:
            role_enum = Role(row["role"])
        except ValueError:
            logger.warning("unknown role in db: %s", row["role"])
            continue
        scope_enum = Scope(row["scope"])
        b = UserRoleBinding(
            role=role_enum,
            scope=scope_enum,
            business_line_id=row["line_id"] if scope_enum == Scope.BUSINESS_LINE else None,
        )
        if b not in bindings:
            bindings.append(b)
        if role_enum.value not in roles:
            roles.append(role_enum.value)

    return CurrentUserV2(
        id=user_row["id"],
        username=user_row["username"],
        display_name=user_row["display_name"],
        email=user_row["email"],
        is_active=user_row["is_active"],
        roles=roles,
        accessible_lines=accessible_lines,
        bindings=bindings,
        active_view=None,
        # M2: 多租户中间件读
        is_super_admin=bool(user_row.get("is_super_admin", False)),
        tenant_id=str(user_row["tenant_id"]) if user_row.get("tenant_id") else None,
    )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_current_user_v2(
    request: Request,
    finbp_token: str | None = Cookie(default=None),
) -> CurrentUserV2:
    """FastAPI dependency: 从 cookie + DB 加载 v2 CurrentUser。

    与 v1 区别:
        • 从 X-Active-View header 读视角切换
        • 返回 CurrentUserV2 而非 CurrentUser
        • 复用 v1 的 cookie / JWT 解析(decode_token / _load_user_by_id)

    Token 解析顺序(与 v1 一致):
      0. X-Service-Token header (BIZ_BP_SERVICE_TOKEN) → 合成 admin+auditor
      1. finbp_token cookie (alias 取自 _cookie_name())
      2. Authorization: Bearer <jwt> header

    任何步骤失败抛 HTTPException(401, ...)。
    """
    # 0. Service-token (in-process service-to-service)
    service_token = os.environ.get("BIZ_BP_SERVICE_TOKEN")
    if service_token:
        supplied = request.headers.get("x-service-token")
        if supplied and supplied == service_token:
            user = CurrentUserV2(
                id=0,
                username="__service__",
                display_name="Internal Service",
                email=None,
                is_active=True,
                roles=[Role.ADMIN.value, Role.AUDITOR.value],
                accessible_lines=[],
                bindings=[
                    UserRoleBinding(
                        role=Role.ADMIN,
                        scope=Scope.GLOBAL,
                        business_line_id=None,
                    ),
                    UserRoleBinding(
                        role=Role.AUDITOR,
                        scope=Scope.GLOBAL,
                        business_line_id=None,
                    ),
                ],
                active_view="admin",
                is_super_admin=True,  # M2: service account 当 super admin
                tenant_id=None,  # 走默认 tenant (DEFAULT_TENANT_ID)
            )
            # M2: 写 request.state 给 tenant_context 读
            request.state.current_user = user
            return user

    # 1. cookie (parameter name 匹配默认 cookie 名 "finbp_token";
    #    若环境变量 BIZ_BP_COOKIE_NAME 改过名,fallback 到 request.cookies)
    token = finbp_token
    if not token:
        cookie_name = _cookie_name()
        if cookie_name and cookie_name != "finbp_token":
            token = request.cookies.get(cookie_name)
    # 2. Authorization: Bearer
    if not token:
        auth = (
            request.headers.get("authorization")
            or request.headers.get("Authorization")
        )
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. decode + 加载 user
    payload = decode_token(token)
    user = await load_user_v2(payload.sub)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user no longer exists or is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4. 视角切换(可选)— X-Active-View header
    active_view = (
        request.headers.get("x-active-view")
        or request.headers.get("X-Active-View")
    )
    if active_view:
        user = switch_view(user, active_view)

    # M2: 写 request.state 给 tenant_context 读
    request.state.current_user = user
    return user


# ---------------------------------------------------------------------------
# 视角切换辅助
# ---------------------------------------------------------------------------


def switch_view(user: CurrentUserV2, view: str) -> CurrentUserV2:
    """显式切换用户视角(fin / hr / line_owner / admin)。"""
    valid_views = {"fin", "hr", "line_owner", "admin", "auditor", "viewer", "none"}
    if view not in valid_views:
        raise HTTPException(400, f"invalid view '{view}', valid: {valid_views}")
    user.active_view = view
    return user


def copilot_view_prompt_suffix(view: str) -> str:
    """返回当前视角对应的 Copilot system_prompt 后缀。"""
    return {
        "fin": (
            "\n\n【FIN 视角约束】"
            "\n你只能回答财务相关问题,严禁回答人力/薪资/招聘问题。"
            "\n看不到的数据直接说'该数据不属于 FIN 视角访问范围'。"
        ),
        "hr": (
            "\n\n【HR 视角约束】"
            "\n你只能回答人力/招聘/绩效/培训相关问题,严禁回答财务/项目利润问题。"
            "\n看不到的数据直接说'该数据不属于 HR 视角访问范围'。"
        ),
        "line_owner": (
            "\n\n【业务线负责人视角】"
            "\n你可以跨域分析(财务+人力+业务),但回答时必须标明数据域来源。"
        ),
        "admin": "",  # admin 全权,无约束
    }.get(view, "")


__all__ = [
    "load_user_v2",
    "get_current_user_v2",
    "switch_view",
    "copilot_view_prompt_suffix",
]
