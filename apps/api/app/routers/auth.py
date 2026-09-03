"""
apps/api/app/routers/auth.py

Authentication + user-management HTTP endpoints.

Routes
------
POST   /api/auth/login                — body {username, password} → set cookie + me
POST   /api/auth/logout               — clear cookie
GET    /api/auth/me                   — current user (id/username/roles/lines)
GET    /api/auth/accessible-lines     — accessible business line ids
GET    /api/auth/users                — admin: list all users
POST   /api/auth/users                — admin: create a user
PATCH  /api/auth/users/{id}/roles     — admin: replace a user's roles + lines
DELETE /api/auth/users/{id}           — admin: deactivate a user
GET    /api/auth/audit-log            — admin/auditor: query the audit log
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
from ..core.config import get_settings
from ..core.logging import get_logger
from ..core.rbac import (
    filter_accessible_lines,
    require_admin_dep,
    require_auditor_or_admin_dep,
)
from ..core.registry import load_registry
from ..db.session import get_session_factory
from ..schemas.auth import (
    AccessibleLinesResponse,
    AuditLogItem,
    AuditLogResponse,
    CreateUserRequest,
    CurrentUserResponse,
    LoginRequest,
    LogoutResponse,
    UpdateUserRolesRequest,
    UserListItem,
    UserListResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def _cookie_settings() -> dict:
    """Return kwargs for ``Response.set_cookie`` derived from env / config."""
    settings = get_settings()
    secure_env = os.environ.get("FIN_BP_COOKIE_SECURE")
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
        # Same error message for "no such user" / "wrong password" /
        # "inactive user" to avoid leaking which usernames exist.
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


async def _load_user_with_perms(user_id: int) -> UserListItem | None:
    factory = get_session_factory()
    async with factory() as session:
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
    return UserListItem(
        id=int(u["id"]),
        username=str(u["username"]),
        display_name=str(u["display_name"] or u["username"]),
        email=u["email"],
        is_active=bool(u["is_active"]),
        roles=[str(r) for r in (roles or [])],
        accessible_lines=[str(x) for x in (lines or [])],
        created_at=str(u["created_at"]),
    )


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="Admin: list all users",
)
async def list_users(
    user: CurrentUser = Depends(require_admin_dep),
) -> UserListResponse:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text("SELECT id FROM users ORDER BY id")
            )
        ).scalars().all()
    items: list[UserListItem] = []
    for uid in rows:
        item = await _load_user_with_perms(int(uid))
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
) -> UserListItem:
    factory = get_session_factory()
    pwd_hash = hash_password(body.password)
    async with factory() as session:
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
    item = await _load_user_with_perms(int(new_id))
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
) -> UserListItem:
    if not body.roles and body.accessible_lines is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="nothing to update (pass roles=[] to clear, or accessible_lines)",
        )
    # Refuse to demote the only admin (safety: at least one admin must exist)
    if "admin" in body.roles or "admin" not in body.roles:
        # only check if the target currently has admin and we're demoting
        factory = get_session_factory()
        async with factory() as session:
            target_roles = set(
                (
                    await session.execute(
                        text("SELECT role FROM user_roles WHERE user_id = :uid"),
                        {"uid": user_id},
                    )
                ).scalars().all()
            )
        if "admin" in target_roles and "admin" not in body.roles:
            async with factory() as session:
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
    async with factory() as session:
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
    item = await _load_user_with_perms(user_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user not found: {user_id}",
        )
    return item


@router.delete(
    "/users/{user_id}",
    response_model=LogoutResponse,
    summary="Admin: deactivate a user (soft delete; row kept for audit)",
)
async def deactivate_user(
    user_id: int,
    user: CurrentUser = Depends(require_admin_dep),
) -> LogoutResponse:
    if user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot deactivate yourself",
        )
    factory = get_session_factory()
    async with factory() as session:
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
    factory = get_session_factory()
    async with factory() as session:
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
