"""
apps/api/app/schemas/auth.py

Pydantic models for the /api/auth/* endpoints.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class LogoutResponse(BaseModel):
    ok: bool = True
    message: str = "logged out"


class CurrentUserResponse(BaseModel):
    id: int
    username: str
    display_name: str
    email: Optional[str] = None
    is_active: bool
    roles: list[str]
    accessible_lines: list[str]


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    password: str = Field(..., min_length=6, max_length=256)
    display_name: Optional[str] = Field(default=None, max_length=128)
    email: Optional[EmailStr] = None
    roles: list[str] = Field(default_factory=list)
    accessible_lines: list[str] = Field(default_factory=list)


class UpdateUserRolesRequest(BaseModel):
    """Replace (or merge with) a user's role set."""

    roles: list[str] = Field(..., description="full new role list (replaces existing)")
    accessible_lines: Optional[list[str]] = Field(
        default=None,
        description=(
            "if provided, replaces the user's accessible_lines; otherwise "
            "lines are kept in sync with bp:<line> roles automatically"
        ),
    )


class UpdateUserRequest(BaseModel):
    """Update a user's profile fields (display_name, email, is_active, password).

    All fields are optional — the caller passes only the ones that need
    changing.  When ``password`` is provided the new value is hashed with
    bcrypt before being written; the plain text is never logged.
    """

    display_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    # Explicit "set email to NULL" flag. The UI can't pass an empty
    # string (Pydantic EmailStr would reject it), so we expose a
    # separate signal: clear_email=True writes SQL NULL, clear_email=
    # False leaves the column untouched. This mirrors the pattern
    # used by the AI model router for clearing api_key.
    clear_email: bool = False
    password: Optional[str] = Field(
        default=None,
        min_length=6,
        max_length=256,
        description="plaintext password; hashed before persistence",
    )


class UpdateUserLinesRequest(BaseModel):
    """Replace a user's accessible_lines without touching roles.

    This is a thin, single-purpose endpoint so the admin UI can
    toggle the line list without the dance of having to re-send the
    full role set.
    """

    accessible_lines: list[str] = Field(
        default_factory=list,
        description="full new accessible_lines list (replaces existing)",
    )


class ResetPasswordRequest(BaseModel):
    """Admin-initiated password reset.

    The caller (an admin) supplies a fresh plaintext password; the API
    hashes it and writes it to ``users.password_hash``. The plaintext is
    echoed back in the response so the admin can communicate it to the
    user out-of-band (or just NOT show it — see the ``reveal`` flag).
    """

    new_password: str = Field(..., min_length=6, max_length=256)
    reveal: bool = Field(
        default=False,
        description=(
            "If true, the response includes the plaintext password so the "
            "admin can copy it. Defaults to false (response omits the "
            "secret)."
        ),
    )


class ResetPasswordResponse(BaseModel):
    ok: bool = True
    message: str
    new_password: Optional[str] = None


class UserListItem(BaseModel):
    id: int
    username: str
    display_name: str
    email: Optional[str] = None
    is_active: bool
    roles: list[str]
    accessible_lines: list[str]
    created_at: str
    # v2 RBAC bindings (commit C1, 2026-09-04). Always present, empty list
    # for users whose bindings haven't been migrated to the (role, scope,
    # line_id) triplet schema yet. Additive — never breaks v1 clients
    # that only look at the legacy ``roles`` field above.
    v2_bindings: list[UserRoleBindingResponse] = Field(
        default_factory=list,
        description="v2 RBAC bindings (role + scope + line_id triplets)",
    )


class UserListResponse(BaseModel):
    count: int
    users: list[UserListItem]


# ---------------------------------------------------------------------------
# v2 RBAC bindings (commit C1, 2026-09-04)
#
# These three models are the wire format for PATCH/GET
# /api/auth/users/{id}/v2-roles. The previous ``UpdateUserRolesRequest``
# only accepted ``roles: list[str]`` (v1 style — e.g. "bp:residential")
# and could not express the 8-role × 2-scope × N-line triplet that the
# v2 RBAC matrix (see app/core/rbac_v2.py) needs.
# ---------------------------------------------------------------------------


class UserRoleBindingResponse(BaseModel):
    """Single v2 role binding.

    role  ∈ {admin, auditor, viewer, line_owner, fin_bp, hr_bp,
             fin_bp_global, hr_bp_global}
    scope ∈ {"global", "business_line"}
    line_id is required when scope="business_line" and must be None
    when scope="global". Business-line-scope roles (line_owner / fin_bp
    / hr_bp) MUST be paired with scope="business_line" + a real line
    id; global-only roles (admin / auditor / viewer / *_global) MUST
    be paired with scope="global" and line_id=None. The router layer
    enforces these invariants — Pydantic alone just validates the
    string shape.
    """

    role: str = Field(
        ...,
        description=(
            "v2 角色 id, e.g. fin_bp / hr_bp / line_owner / admin / "
            "auditor / viewer / fin_bp_global / hr_bp_global"
        ),
    )
    scope: str = Field(
        ...,
        description="作用域, global 或 business_line",
    )
    line_id: Optional[str] = Field(
        default=None,
        description="业务线 id (scope=business_line 时必填, scope=global 时必须 None)",
    )

    model_config = {"extra": "forbid"}


class UpdateUserV2RolesRequest(BaseModel):
    """Replace a user's complete v2 role binding set.

    The body is a full replacement (not a patch) — every call must
    include the entire new binding list. An empty ``bindings`` array
    is rejected by the router because the system must keep at least
    one admin in the database; passing an empty list would be a way
    to lock every admin out.
    """

    bindings: list[UserRoleBindingResponse] = Field(
        default_factory=list,
        description=(
            "完整新 bindings 列表 (替换现有), 空数组 = 清除所有 v2 角色 "
            "(router 拒绝空数组以保留至少一个 admin)"
        ),
    )


class UserV2RolesResponse(BaseModel):
    """Response body for GET / PATCH /api/auth/users/{id}/v2-roles."""

    user_id: int
    bindings: list[UserRoleBindingResponse]


class AccessibleLinesResponse(BaseModel):
    count: int
    lines: list[str]
    all_lines: list[str]


class AuditLogItem(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    method: str
    path: str
    query: Optional[str] = None
    status_code: int
    duration_ms: int
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    timestamp: str


class AuditLogResponse(BaseModel):
    count: int
    items: list[AuditLogItem]


__all__ = [
    "AccessibleLinesResponse",
    "AuditLogItem",
    "AuditLogResponse",
    "CreateUserRequest",
    "CurrentUserResponse",
    "LoginRequest",
    "LogoutResponse",
    "ResetPasswordRequest",
    "ResetPasswordResponse",
    "UpdateUserLinesRequest",
    "UpdateUserRequest",
    "UpdateUserRolesRequest",
    "UpdateUserV2RolesRequest",
    "UserListItem",
    "UserListResponse",
    "UserRoleBindingResponse",
    "UserV2RolesResponse",
]
