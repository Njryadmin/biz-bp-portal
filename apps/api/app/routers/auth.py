"""
apps/api/app/routers/auth.py

身份认证 + 用户管理的 HTTP 端点。

路由
----
POST   /api/auth/login                       —— body {username, password} → 设置 cookie + me
POST   /api/auth/logout                      —— 清除 cookie
GET    /api/auth/me                          —— 当前用户（id/username/roles/lines）v1 shape
GET    /api/auth/me-v2                       —— 当前用户 (v2 shape: bindings + active_view, E 2026-09-04)
GET    /api/auth/accessible-lines            —— 当前用户可访问的业务线 id
GET    /api/auth/users                       —— admin：列出全部用户
POST   /api/auth/users                       —— admin：创建用户
PATCH  /api/auth/users/{id}                  —— admin：更新 display_name/email/password/is_active
PATCH  /api/auth/users/{id}/roles            —— admin：替换用户的角色 + 业务线 (v1, 保留)
PATCH  /api/auth/users/{id}/lines            —— admin：仅替换用户的 accessible_lines
GET    /api/auth/users/{id}/v2-roles         —— admin：读取 v2 角色绑定 (commit C1)
PATCH  /api/auth/users/{id}/v2-roles         —— admin：替换 v2 角色绑定 (commit C1)
POST   /api/auth/users/{id}/reset-password   —— admin：轮换用户密码
DELETE /api/auth/users/{id}                  —— admin：停用用户（软删除）
GET    /api/auth/audit-log                   —— admin/auditor：查询审计日志
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text

from ..core.auth import (
    CurrentUser,
    _cookie_name,
    _load_user_by_credentials,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from ..core.auth_v2 import CurrentUserV2, get_current_user_v2
from ..core.config import get_settings
from ..core.logging import get_logger
from ..core.rbac import (
    filter_accessible_lines,
    require_admin_dep,
    require_auditor_or_admin_dep,
)
from ..core.registry import load_registry
from ..core.tenant_context import TenantContext, get_tenant_context
from ..db.session import get_session_factory  # kept for test mocks / plugin extensions
from ..db.tenant import tenant_session
from ..schemas.auth import (
    AccessibleLinesResponse,
    AuditLogItem,
    AuditLogResponse,
    CreateUserRequest,
    CurrentUserResponse,
    LoginRequest,
    LogoutResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    UpdateUserLinesRequest,
    UpdateUserRequest,
    UpdateUserRolesRequest,
    UpdateUserV2RolesRequest,
    UserListItem,
    UserListResponse,
    UserRoleBindingResponse,
    UserV2RolesResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# v2 RBAC binding helpers (commit C1, 2026-09-04)
#
# Three role classes that drive the wire-level validation in
# PATCH /api/auth/users/{id}/v2-roles. Defined here (not in
# app.core.rbac_v2) because they're an admin-UI concern — the rbac_v2
# module stays focused on permission checks and has no business
# knowing about HTTP request bodies.
# ---------------------------------------------------------------------------

# All 8 v2 roles. Duplicated from app.core.rbac_v2.Role to avoid
# importing a heavy module just for a 8-element set; the source of
# truth for the matrix still lives in rbac_v2.
_ROLE_ENUM_VALUES: frozenset[str] = frozenset(
    {
        "admin",
        "auditor",
        "viewer",
        "line_owner",
        "fin_bp",
        "hr_bp",
        "fin_bp_global",
        "hr_bp_global",
    }
)

# Roles that must always carry scope=business_line + a real line id.
# fin_bp_global / hr_bp_global are global by design.
_LINE_SCOPED_ROLES: frozenset[str] = frozenset(
    {"line_owner", "fin_bp", "hr_bp"}
)

# Roles that must always carry scope=global + line_id=None. The set
# mirrors admin/auditor/viewer + the two *_global variants.
_GLOBAL_ROLES: frozenset[str] = frozenset(
    {
        "admin",
        "auditor",
        "viewer",
        "fin_bp_global",
        "hr_bp_global",
    }
)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def _cookie_settings() -> dict:
    """Return kwargs for ``Response.set_cookie`` derived from env / config."""
    settings = get_settings()
    secure_env = os.environ.get("BIZ_BP_COOKIE_SECURE")
    secure = (
        secure_env.lower() in {"1", "true", "yes"}
        if secure_env is not None
        else settings.cookie_secure
    )
    return {
        "key": _cookie_name(),
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
    }


def _set_token_cookie(response: Response, token: str, *, max_age_seconds: int) -> None:
    response.set_cookie(value=token, max_age=max_age_seconds, **_cookie_settings())


def _clear_token_cookie(response: Response) -> None:
    response.delete_cookie(**_cookie_settings())


def _cookie_max_age() -> int:
    return int(os.environ.get("JWT_EXPIRY_HOURS") or "24") * 3600


# ---------------------------------------------------------------------------
# /api/auth/login
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=CurrentUserResponse,
    summary="Authenticate with username + password; receive an httpOnly cookie",
)
async def login(body: LoginRequest, response: Response) -> CurrentUserResponse:
    user = await _load_user_by_credentials(body.username, body.password)
    if user is None:
        # "用户不存在" / "密码错误" / "账号已停用" 返回同一条错误信息，
        # 避免泄露系统中存在的用户名。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    token = create_access_token(
        user_id=user.id,
        username=user.username,
        roles=user.roles,
        accessible_lines=user.accessible_lines,
    )
    _set_token_cookie(response, token, max_age_seconds=_cookie_max_age())
    logger.info(
        "login: user=%s roles=%s lines=%s",
        user.username, user.roles, user.accessible_lines,
    )
    return CurrentUserResponse(**user.to_public_dict())


# ---------------------------------------------------------------------------
# /api/auth/logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Clear the auth cookie",
)
async def logout(response: Response) -> LogoutResponse:
    _clear_token_cookie(response)
    return LogoutResponse(ok=True, message="logged out")


# ---------------------------------------------------------------------------
# /api/auth/me
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    summary="Return the currently authenticated user",
)
async def me(user: CurrentUser = Depends(get_current_user)) -> CurrentUserResponse:
    return CurrentUserResponse(**user.to_public_dict())


# ---------------------------------------------------------------------------
# /api/auth/me-v2 — v2 shape with bindings + active_view (E, 2026-09-04)
#
# Why a separate endpoint instead of a query flag on /me?
#   * The v1 /me shape is the wire contract for the existing frontend
#     (Topbar / SidebarMenu / etc.) — adding a `bindings` field would
#     change the type even when no caller asked for it.
#   * The PerspectiveSwitcher is the *only* consumer that needs the v2
#     shape, and it only loads on the dashboard layout — so an extra
#     round-trip is acceptable.
# ---------------------------------------------------------------------------


@router.get(
    "/me-v2",
    summary="Return the currently authenticated user in v2 shape (bindings + active_view)",
)
async def me_v2(user: CurrentUserV2 = Depends(get_current_user_v2)) -> dict:
    """v2 shape: includes ``bindings`` (role + scope + line_id) and the
    currently active view (``active_view`` from the X-Active-View header).

    The frontend (PerspectiveSwitcher) uses this to:
      * pick the default view segment (fin / hr / shared / line_owner / admin)
      * render the role-based menu
      * keep the localStorage ``biz-bp.active_view`` key in sync
    """
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "is_active": user.is_active,
        "roles": list(user.roles),
        "accessible_lines": list(user.accessible_lines),
        "bindings": [
            {
                "role": b.role.value,
                "scope": b.scope.value,
                "line_id": b.business_line_id,
            }
            for b in user.bindings
        ],
        "active_view": user.active_view,
    }


# ---------------------------------------------------------------------------
# /api/auth/accessible-lines
# ---------------------------------------------------------------------------


@router.get(
    "/accessible-lines",
    response_model=AccessibleLinesResponse,
    summary="List the business lines the current user can see",
)
async def accessible_lines(
    user: CurrentUser = Depends(get_current_user),
) -> AccessibleLinesResponse:
    all_ids = [e.line.id for e in load_registry()]
    return AccessibleLinesResponse(
        count=len(user.accessible_lines),
        lines=user.accessible_lines,
        all_lines=all_ids,
    )


# ---------------------------------------------------------------------------
# /api/auth/users — admin-only management
# ---------------------------------------------------------------------------


async def _load_user_with_perms(
    user_id: int, ctx: TenantContext
) -> UserListItem | None:
    # M2: 用 tenant_session 让 RLS policy 放行; bypass_rls=True 让 super admin
    # 能查任意租户的用户 (admin 跨租户用户管理用)
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        u = (
            await session.execute(
                text(
                    "SELECT id, username, display_name, email, is_active, "
                    "created_at FROM users WHERE id = :uid"
                ),
                {"uid": user_id},
            )
        ).mappings().first()
        if not u:
            return None
        roles = (
            await session.execute(
                text("SELECT role FROM user_roles WHERE user_id = :uid ORDER BY role"),
                {"uid": user_id},
            )
        ).scalars().all()
        lines = (
            await session.execute(
                text(
                    "SELECT line_id FROM user_business_lines "
                    "WHERE user_id = :uid ORDER BY line_id"
                ),
                {"uid": user_id},
            )
        ).scalars().all()
        # v2 bindings — additive (commit C1, 2026-09-04). We pull
        # scope/line_id from user_roles so the admin UI can render
        # the full triplet without a second round-trip. Rows whose
        # scope is NULL (i.e. migration 001 hasn't run on this DB) are
        # still surfaced as legacy entries with scope=``"legacy"`` and
        # line_id=``None`` so the UI can flag them as "needs migration".
        v2_rows = (
            await session.execute(
                text(
                    "SELECT role, scope, line_id FROM user_roles "
                    "WHERE user_id = :uid ORDER BY role, line_id"
                ),
                {"uid": user_id},
            )
        ).mappings().all()
    v2_bindings: list[UserRoleBindingResponse] = []
    for r in v2_rows or []:
        scope_val = r["scope"] or "legacy"
        v2_bindings.append(
            UserRoleBindingResponse(
                role=str(r["role"]),
                scope=str(scope_val),
                line_id=r["line_id"],
            )
        )
    return UserListItem(
        id=int(u["id"]),
        username=str(u["username"]),
        display_name=str(u["display_name"] or u["username"]),
        email=u["email"],
        is_active=bool(u["is_active"]),
        roles=[str(r) for r in (roles or [])],
        accessible_lines=[str(x) for x in (lines or [])],
        created_at=str(u["created_at"]),
        v2_bindings=v2_bindings,
    )


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="Admin: list all users",
)
async def list_users(
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserListResponse:
    # M2: 走 tenant_session 满足 RLS. 跨租户场景下 super admin 通过
    # bypass_rls 看全部; 普通 admin 只看自己租户.
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        rows = (
            await session.execute(
                text("SELECT id FROM users ORDER BY id")
            )
        ).scalars().all()
    items: list[UserListItem] = []
    for uid in rows:
        item = await _load_user_with_perms(int(uid), ctx)
        if item is not None:
            items.append(item)
    return UserListResponse(count=len(items), users=items)


@router.post(
    "/users",
    response_model=UserListItem,
    status_code=status.HTTP_201_CREATED,
    summary="Admin: create a new user",
)
async def create_user(
    body: CreateUserRequest,
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserListItem:
    pwd_hash = hash_password(body.password)
    # M2: 走 tenant_session; trigger set_tenant_from_guc 会从 GUC 自动填 tenant_id.
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        # Check for duplicate username
        existing = (
            await session.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": body.username},
            )
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"username already exists: {body.username}",
            )
        new_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO users
                        (username, email, password_hash, display_name, is_active)
                    VALUES
                        (:username, :email, :password_hash, :display_name, TRUE)
                    RETURNING id
                    """
                ),
                {
                    "username": body.username,
                    "email": body.email,
                    "password_hash": pwd_hash,
                    "display_name": body.display_name or body.username,
                },
            )
        ).scalar_one()
        for role in body.roles:
            await session.execute(
                text(
                    "INSERT INTO user_roles (user_id, role) "
                    "VALUES (:uid, :role) ON CONFLICT DO NOTHING"
                ),
                {"uid": int(new_id), "role": role},
            )
        # Derive business lines from bp:<line> roles
        derived_lines = {
            r[3:] for r in body.roles if r.startswith("bp:")
        }
        for line in (body.accessible_lines or []) + list(derived_lines):
            await session.execute(
                text(
                    "INSERT INTO user_business_lines (user_id, line_id) "
                    "VALUES (:uid, :line_id) ON CONFLICT DO NOTHING"
                ),
                {"uid": int(new_id), "line_id": line},
            )
        await session.commit()
    item = await _load_user_with_perms(int(new_id), ctx)
    assert item is not None
    return item


@router.patch(
    "/users/{user_id}/roles",
    response_model=UserListItem,
    summary="Admin: replace a user's roles + business-line access",
)
async def update_user_roles(
    user_id: int,
    body: UpdateUserRolesRequest,
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserListItem:
    if not body.roles and body.accessible_lines is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="nothing to update (pass roles=[] to clear, or accessible_lines)",
        )
    # Refuse to demote the only admin (safety: at least one admin must exist)
    if "admin" in body.roles or "admin" not in body.roles:
        # only check if the target currently has admin and we're demoting
        async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
            target_roles = set(
                (
                    await session.execute(
                        text("SELECT role FROM user_roles WHERE user_id = :uid"),
                        {"uid": user_id},
                    )
                ).scalars().all()
            )
        if "admin" in target_roles and "admin" not in body.roles:
            async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
                other_admins = (
                    await session.execute(
                        text(
                            "SELECT COUNT(DISTINCT user_id) FROM user_roles "
                            "WHERE role = 'admin' AND user_id <> :uid"
                        ),
                        {"uid": user_id},
                    )
                ).scalar_one()
            if int(other_admins) == 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="cannot demote the last admin",
                )
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        await session.execute(
            text("DELETE FROM user_roles WHERE user_id = :uid"),
            {"uid": user_id},
        )
        for role in body.roles:
            await session.execute(
                text(
                    "INSERT INTO user_roles (user_id, role) "
                    "VALUES (:uid, :role) ON CONFLICT DO NOTHING"
                ),
                {"uid": user_id, "role": role},
            )
        # Business-line access:
        # 1) Always recompute from bp:<line> roles
        derived = {r[3:] for r in body.roles if r.startswith("bp:")}
        # 2) Merge with explicit accessible_lines if provided
        if body.accessible_lines is not None:
            explicit = set(body.accessible_lines)
        else:
            # keep existing explicit lines
            existing = set(
                (
                    await session.execute(
                        text(
                            "SELECT line_id FROM user_business_lines WHERE user_id = :uid"
                        ),
                        {"uid": user_id},
                    )
                ).scalars().all()
            )
            explicit = existing
        merged = sorted(explicit | derived)
        await session.execute(
            text("DELETE FROM user_business_lines WHERE user_id = :uid"),
            {"uid": user_id},
        )
        for line in merged:
            await session.execute(
                text(
                    "INSERT INTO user_business_lines (user_id, line_id) "
                    "VALUES (:uid, :line_id) ON CONFLICT DO NOTHING"
                ),
                {"uid": user_id, "line_id": line},
            )
        await session.commit()
    item = await _load_user_with_perms(user_id, ctx)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user not found: {user_id}",
        )
    return item


# ---------------------------------------------------------------------------
# PATCH /api/auth/users/{id}  — general field updates
# ---------------------------------------------------------------------------


@router.patch(
    "/users/{user_id}",
    response_model=UserListItem,
    summary="Admin: update a user's profile (display_name, email, is_active, password)",
)
async def update_user(
    user_id: int,
    body: UpdateUserRequest,
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserListItem:
    """Edit a single user's profile fields. Empty body = no-op (still 200).

    Does NOT touch roles or accessible_lines — use
    ``PATCH /users/{id}/roles`` or ``PATCH /users/{id}/lines`` for those.
    """
    if all(
        v is None
        for v in (body.display_name, body.email, body.is_active, body.password)
    ) and not body.clear_email:
        # Nothing to do — return current state so the UI can re-fetch cheaply.
        # NOTE: clear_email is checked separately because it's a bool flag
        # (not None when the caller wants to clear the email column).
        item = await _load_user_with_perms(user_id, ctx)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user not found: {user_id}",
            )
        return item

    # Last-admin protection: refuse self-deactivation (operators can
    # always use ``DELETE /users/{id}`` instead, which has the same
    # guard). If the admin is deactivating ANOTHER admin and that
    # admin is the last one, refuse.
    if body.is_active is False:
        if user_id == user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cannot deactivate yourself",
            )
        async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
            target_roles = set(
                (
                    await session.execute(
                        text("SELECT role FROM user_roles WHERE user_id = :uid"),
                        {"uid": user_id},
                    )
                ).scalars().all()
            )
        if "admin" in target_roles:
            async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
                other_admins = (
                    await session.execute(
                        text(
                            "SELECT COUNT(DISTINCT user_id) FROM user_roles "
                            "WHERE role = 'admin' AND user_id <> :uid"
                        ),
                        {"uid": user_id},
                    )
                ).scalar_one()
            if int(other_admins) == 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="cannot deactivate the last admin",
                )

    set_clauses: list[str] = []
    params: dict[str, object] = {"uid": user_id}
    if body.display_name is not None:
        set_clauses.append("display_name = :display_name")
        params["display_name"] = body.display_name
    if body.clear_email:
        # Explicit clear wins over any other email value.
        set_clauses.append("email = NULL")
    elif body.email is not None:
        set_clauses.append("email = :email")
        params["email"] = body.email
    if body.is_active is not None:
        set_clauses.append("is_active = :is_active")
        params["is_active"] = bool(body.is_active)
    if body.password is not None:
        set_clauses.append("password_hash = :password_hash")
        params["password_hash"] = hash_password(body.password)
        logger.info("update_user: admin=%s rotated password for uid=%s", user.username, user_id)
    if not set_clauses:
        # Defensive — should be unreachable because of the early-return above
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no updatable fields supplied",
        )
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        result = await session.execute(
            text(
                f"UPDATE users SET {', '.join(set_clauses)} WHERE id = :uid"
            ),
            params,
        )
        if result.rowcount == 0:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user not found: {user_id}",
            )
        await session.commit()
    item = await _load_user_with_perms(user_id, ctx)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user not found: {user_id}",
        )
    return item


# ---------------------------------------------------------------------------
# PATCH /api/auth/users/{id}/lines  — accessible_lines only
# ---------------------------------------------------------------------------


@router.patch(
    "/users/{user_id}/lines",
    response_model=UserListItem,
    summary="Admin: replace a user's accessible_lines (roles untouched)",
)
async def update_user_lines(
    user_id: int,
    body: UpdateUserLinesRequest,
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserListItem:
    """Single-purpose endpoint so the admin UI can edit the line list
    without re-sending the full role set.

    Implementation note: ``accessible_lines`` is always derived as the
    union of (a) the explicit ``user_business_lines`` rows and (b) the
    lines implied by the user's ``bp:<line>`` roles. We therefore keep
    the existing ``bp:`` roles intact and only replace the explicit
    rows.
    """
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        # Reject unknown user (FK would also reject, but the 404 message
        # is friendlier).
        target_exists = (
            await session.execute(
                text("SELECT 1 FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
        ).first()
        if not target_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user not found: {user_id}",
            )
        derived = {
            r[3:]
            for r in (
                await session.execute(
                    text(
                        "SELECT role FROM user_roles "
                        "WHERE user_id = :uid AND role LIKE 'bp:%'"
                    ),
                    {"uid": user_id},
                )
            ).scalars().all()
        }
        explicit = set(body.accessible_lines or [])
        merged = sorted(explicit | derived)
        await session.execute(
            text("DELETE FROM user_business_lines WHERE user_id = :uid"),
            {"uid": user_id},
        )
        for line in merged:
            await session.execute(
                text(
                    "INSERT INTO user_business_lines (user_id, line_id) "
                    "VALUES (:uid, :line_id) ON CONFLICT DO NOTHING"
                ),
                {"uid": user_id, "line_id": line},
            )
        await session.commit()
    item = await _load_user_with_perms(user_id, ctx)
    assert item is not None
    return item


# ---------------------------------------------------------------------------
# v2 RBAC bindings  — GET / PATCH /api/auth/users/{id}/v2-roles
#
# Added in commit C1 (2026-09-04) to express the 8-role × 2-scope × N-line
# triplet that the v1 ``PATCH /users/{id}/roles`` (which only takes
# ``roles: list[str]``) cannot represent. The v1 endpoint is kept
# untouched — v1 web clients still call it; the new triplet endpoint
# is additive and lives at a distinct URL so the two are easy to
# distinguish in audit logs.
#
# Business rules enforced here (everything is 400 unless noted):
#   • At least one ``admin`` binding must remain in the DB. The last
#     admin cannot demote themselves — returns 409.
#   • Every binding's (role, scope, line_id) must be self-consistent:
#       - scope="business_line"  → line_id is a non-empty string
#       - scope="global"         → line_id is None
#       - line-scoped roles     (line_owner / fin_bp / hr_bp) must
#         carry scope="business_line"
#       - global-only roles     (admin / auditor / viewer / *_global)
#         must carry scope="global"
#   • (role, line_id) is unique within one request.
# Side effect: the ``user_business_lines`` table is rewritten from
# the union of the new bindings' line_ids so v1 ``accessible_lines``
# stays in sync (v1 endpoints and the v1 /me payload still read it).
# ---------------------------------------------------------------------------


@router.get(
    "/users/{user_id}/v2-roles",
    response_model=UserV2RolesResponse,
    summary="Admin: read a user's v2 role bindings (role + scope + line_id)",
)
async def get_user_v2_roles(
    user_id: int,
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserV2RolesResponse:
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        target_exists = (
            await session.execute(
                text("SELECT 1 FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
        ).first()
        if not target_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user not found: {user_id}",
            )
        rows = (
            await session.execute(
                text(
                    "SELECT role, scope, line_id FROM user_roles "
                    "WHERE user_id = :uid ORDER BY role, line_id"
                ),
                {"uid": user_id},
            )
        ).mappings().all()
    return UserV2RolesResponse(
        user_id=user_id,
        bindings=[
            UserRoleBindingResponse(
                role=str(r["role"]),
                scope=str(r["scope"] or "legacy"),
                line_id=r["line_id"],
            )
            for r in rows
        ],
    )


@router.patch(
    "/users/{user_id}/v2-roles",
    response_model=UserV2RolesResponse,
    summary=(
        "Admin: replace a user's v2 role bindings (role + scope + line_id)"
    ),
)
async def update_user_v2_roles(
    user_id: int,
    body: UpdateUserV2RolesRequest,
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserV2RolesResponse:
    """Replace a user's complete v2 role binding set.

    See the section header above for the full business-rule list.
    Returns the new binding set (so the admin UI can re-render
    without a follow-up GET). Raises 400 for any rule violation and
    409 if the operation would remove the last admin.
    """
    # 1) Shape validation. The Pydantic model already guarantees the
    #    fields exist and have the right types; here we add the
    #    cross-field invariants the JSON schema cannot express.
    if not body.bindings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "bindings cannot be empty — pass at least one admin "
                "binding to keep system access (last-admin protection)"
            ),
        )
    seen: set[tuple[str, str | None]] = set()
    for b in body.bindings:
        if b.role not in _ROLE_ENUM_VALUES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown role: {b.role!r} (allowed: {sorted(_ROLE_ENUM_VALUES)})",
            )
        if b.scope not in ("global", "business_line"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown scope: {b.scope!r} (allowed: 'global', 'business_line')",
            )
        if b.scope == "business_line" and not b.line_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"role {b.role!r} with scope='business_line' must "
                    f"carry a non-empty line_id"
                ),
            )
        if b.scope == "global" and b.line_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"role {b.role!r} with scope='global' must not "
                    f"carry line_id (got {b.line_id!r})"
                ),
            )
        if b.role in _LINE_SCOPED_ROLES and b.scope != "business_line":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"role {b.role!r} is line-scoped and must be paired "
                    f"with scope='business_line' (got {b.scope!r})"
                ),
            )
        if b.role in _GLOBAL_ROLES and b.scope != "global":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"role {b.role!r} is global-only and must be paired "
                    f"with scope='global' (got {b.scope!r})"
                ),
            )
        key = (b.role, b.line_id)
        if key in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"duplicate binding: role={b.role!r} line_id={b.line_id!r}",
            )
        seen.add(key)

    # 2) Last-admin protection. We only need to block the call when
    #    (a) the target currently has admin, (b) the new bindings
    #    don't include admin, and (c) no other user in the system
    #    still has admin. Anyone with multiple admins can freely
    #    demote one of them; an admin can give up their own admin
    #    role only if at least one peer admin remains.
    new_has_admin = any(b.role == "admin" for b in body.bindings)
    # Always verify the user exists, regardless of the new bindings'
    # admin status — the FK on user_roles would otherwise turn a
    # missing user into a 500 instead of a friendly 404. We also
    # need the current role set for the last-admin check below.
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        target_exists = (
            await session.execute(
                text("SELECT 1 FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
        ).first()
        if not target_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user not found: {user_id}",
            )
        target_roles = set(
            (
                await session.execute(
                    text("SELECT role FROM user_roles WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            ).scalars().all()
        )
    if not new_has_admin:
        if "admin" in target_roles:
            async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
                other_admins = (
                    await session.execute(
                        text(
                            "SELECT COUNT(DISTINCT user_id) FROM user_roles "
                            "WHERE role = 'admin' AND user_id <> :uid"
                        ),
                        {"uid": user_id},
                    )
                ).scalar_one()
            if int(other_admins) == 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="cannot remove the last admin role from the system",
                )

    # 3) Persist. We rewrite user_roles from scratch and resync
    #    user_business_lines from the union of the new line_ids so
    #    v1 endpoints (which read user_business_lines) stay accurate.
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        await session.execute(
            text("DELETE FROM user_roles WHERE user_id = :uid"),
            {"uid": user_id},
        )
        for b in body.bindings:
            await session.execute(
                text(
                    "INSERT INTO user_roles (user_id, role, scope, line_id) "
                    "VALUES (:uid, :role, :scope, :line_id)"
                ),
                {
                    "uid": user_id,
                    "role": b.role,
                    "scope": b.scope,
                    "line_id": b.line_id,
                },
            )
        derived_lines = sorted({b.line_id for b in body.bindings if b.line_id})
        await session.execute(
            text("DELETE FROM user_business_lines WHERE user_id = :uid"),
            {"uid": user_id},
        )
        for line_id in derived_lines:
            await session.execute(
                text(
                    "INSERT INTO user_business_lines (user_id, line_id) "
                    "VALUES (:uid, :line_id) ON CONFLICT DO NOTHING"
                ),
                {"uid": user_id, "line_id": line_id},
            )
        await session.commit()
    logger.info(
        "update_user_v2_roles: admin=%s replaced v2 bindings for uid=%s "
        "(%d bindings, %d lines)",
        user.username, user_id, len(body.bindings), len(derived_lines),
    )
    return UserV2RolesResponse(user_id=user_id, bindings=body.bindings)


# ---------------------------------------------------------------------------
# POST /api/auth/users/{id}/reset-password  — admin-initiated password rotate
# ---------------------------------------------------------------------------


@router.post(
    "/users/{user_id}/reset-password",
    response_model=ResetPasswordResponse,
    summary="Admin: rotate a user's password",
)
async def reset_user_password(
    user_id: int,
    body: ResetPasswordRequest,
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> ResetPasswordResponse:
    new_hash = hash_password(body.new_password)
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        result = await session.execute(
            text(
                "UPDATE users SET password_hash = :h WHERE id = :uid"
            ),
            {"h": new_hash, "uid": user_id},
        )
        if result.rowcount == 0:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user not found: {user_id}",
            )
        await session.commit()
    logger.info(
        "reset_user_password: admin=%s rotated password for uid=%s reveal=%s",
        user.username, user_id, body.reveal,
    )
    return ResetPasswordResponse(
        ok=True,
        message=f"password rotated for user {user_id}",
        new_password=body.new_password if body.reveal else None,
    )


@router.delete(
    "/users/{user_id}",
    response_model=LogoutResponse,
    summary="Admin: deactivate a user (soft delete; row kept for audit)",
)
async def deactivate_user(
    user_id: int,
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> LogoutResponse:
    if user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot deactivate yourself",
        )
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        # If demoting an admin, refuse when it's the last one
        target_roles = set(
            (
                await session.execute(
                    text("SELECT role FROM user_roles WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            ).scalars().all()
        )
        if "admin" in target_roles:
            other_admins = (
                await session.execute(
                    text(
                        "SELECT COUNT(DISTINCT user_id) FROM user_roles "
                        "WHERE role = 'admin' AND user_id <> :uid"
                    ),
                    {"uid": user_id},
                )
            ).scalar_one()
            if int(other_admins) == 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="cannot deactivate the last admin",
                )
        result = await session.execute(
            text("UPDATE users SET is_active = FALSE WHERE id = :uid"),
            {"uid": user_id},
        )
        await session.commit()
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user not found: {user_id}",
        )
    return LogoutResponse(ok=True, message=f"deactivated user {user_id}")


# ---------------------------------------------------------------------------
# /api/auth/audit-log
# ---------------------------------------------------------------------------


@router.get(
    "/audit-log",
    response_model=AuditLogResponse,
    summary="Admin / auditor: paginated access log",
)
async def get_audit_log(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_id: Optional[int] = Query(None, description="filter by user_id"),
    path_prefix: Optional[str] = Query(None, description="filter by path prefix"),
    user: CurrentUser = Depends(require_auditor_or_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AuditLogResponse:
    where = ["1=1"]
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if user_id is not None:
        where.append("user_id = :uid")
        params["uid"] = user_id
    if path_prefix is not None:
        where.append("path LIKE :pfx")
        params["pfx"] = f"{path_prefix}%"
    where_sql = " AND ".join(where)
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        count_row = (
            await session.execute(
                text(f"SELECT COUNT(*) FROM raw.audit_log WHERE {where_sql}"),
                params,
            )
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT id, user_id, username, method, path, query,
                           status_code, duration_ms, ip, user_agent, "timestamp"
                    FROM raw.audit_log
                    WHERE {where_sql}
                    ORDER BY "timestamp" DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
        ).mappings().all()
    items = [
        AuditLogItem(
            id=int(r["id"]),
            user_id=int(r["user_id"]) if r["user_id"] is not None else None,
            username=r["username"],
            method=str(r["method"]),
            path=str(r["path"]),
            query=r["query"],
            status_code=int(r["status_code"]),
            duration_ms=int(r["duration_ms"]),
            ip=r["ip"],
            user_agent=r["user_agent"],
            timestamp=str(r["timestamp"]),
        )
        for r in rows
    ]
    return AuditLogResponse(count=int(count_row), items=items)


__all__ = ["router"]
