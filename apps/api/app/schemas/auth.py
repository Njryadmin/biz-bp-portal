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


class UserListResponse(BaseModel):
    count: int
    users: list[UserListItem]


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
    "UserListItem",
    "UserListResponse",
]
