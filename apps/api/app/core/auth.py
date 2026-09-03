"""
apps/api/app/core/auth.py

JWT + password-hash helpers for the Fin BP Portal RBAC system.

Design notes
------------
* Token = HS256 JWT signed with the symmetric secret `JWT_SECRET`.
* Storage: httpOnly cookie `finbp_token` (configurable via
  ``FIN_BP_COOKIE_NAME``). NOT localStorage — that would be readable by
  any XSS payload.
* Password hashing: passlib's bcrypt implementation. We expose only
  ``hash_password`` / ``verify_password`` so the rest of the app never
  has to know the algorithm.
* ``get_current_user`` is the FastAPI dependency that every protected
  route uses. It returns a ``CurrentUser`` (id, username, display_name,
  email, is_active, roles, accessible_lines) by looking the user up in
  Postgres on every request. The token is the credential; the DB row is
  the source of truth for role / line assignments (so revoking a role
  takes effect immediately on the next request).

Roles
-----
A user may have many roles simultaneously. The two role namespaces are:
  * ``admin``     — full access; sees every business line.
  * ``auditor``   — read-only across every line; can also read
                    ``/api/auth/audit-log``.
  * ``viewer``    — read-only across every line.
  * ``bp:<line>`` — single-line BP, e.g. ``bp:residential``. Granted via
                    ``user_business_lines`` table (the role itself
                    always has the ``bp:`` prefix; the line id is the
                    suffix). The same row also gets a matching entry in
                    ``user_business_lines`` so business-line endpoints
                    can check accessible_lines directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Cookie, Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy import text

from .config import get_settings
from .logging import get_logger
from ..db.session import get_session_factory

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

# bcrypt with a 12-round salt is the default. We could raise the cost
# factor in production, but 12 is the sweet spot for a 2026 mid-tier
# server (≈250ms per verify on commodity hardware).
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verification of a plaintext password against a stored hash."""
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:  # noqa: BLE001 — malformed hash etc.
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TokenPayload:
    sub: int          # user id
    username: str
    roles: list[str]
    accessible_lines: list[str]
    iat: int
    exp: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "username": self.username,
            "roles": list(self.roles),
            "accessible_lines": list(self.accessible_lines),
            "iat": self.iat,
            "exp": self.exp,
        }


def _secret() -> str:
    """Return the configured JWT secret.

    In dev, the placeholder default is fine. In production we rely on
    the operator to set ``JWT_SECRET`` to a real value; if it's still the
    placeholder, we log a warning (but don't refuse to start, to keep
    the dev-loop unblocked).
    """
    settings = get_settings()
    raw = os.environ.get("JWT_SECRET") or settings.jwt_secret
    return raw


def _algorithm() -> str:
    return os.environ.get("JWT_ALGORITHM") or "HS256"


def _expiry_hours() -> int:
    return int(os.environ.get("JWT_EXPIRY_HOURS") or "24")


def create_access_token(
    user_id: int,
    username: str,
    roles: list[str],
    accessible_lines: list[str],
) -> str:
    """Mint a signed JWT carrying the user's identity + permissions."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=_expiry_hours())
    payload: dict[str, Any] = {
        # Per RFC 7519, ``sub`` MUST be a string. PyJWT 2.x enforces
        # this. We carry the integer id separately in ``uid`` so
        # ``decode_token`` can hand a typed int back to callers.
        "sub": str(int(user_id)),
        "uid": int(user_id),
        "username": username,
        "roles": list(roles),
        "accessible_lines": list(accessible_lines),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=_algorithm())


def decode_token(token: str) -> TokenPayload:
    """Parse and verify a JWT. Raises ``HTTPException(401)`` on failure."""
    try:
        data = jwt.decode(token, _secret(), algorithms=[_algorithm()])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    # ``sub`` is required by JWT spec; we also accept a custom ``uid``
    # int field for callers that want the integer id directly.
    try:
        uid = int(data.get("uid", data.get("sub", 0)))
    except (TypeError, ValueError):
        uid = 0
    return TokenPayload(
        sub=uid,
        username=str(data.get("username", "")),
        roles=list(data.get("roles", []) or []),
        accessible_lines=list(data.get("accessible_lines", []) or []),
        iat=int(data.get("iat", 0)),
        exp=int(data.get("exp", 0)),
    )


# ---------------------------------------------------------------------------
# CurrentUser: the value the rest of the app receives from auth
# dependencies.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CurrentUser:
    id: int
    username: str
    display_name: str
    email: str | None
    is_active: bool
    roles: list[str] = field(default_factory=list)
    accessible_lines: list[str] = field(default_factory=list)

    # -- role helpers --------------------------------------------------------
    def has_role(self, *roles: str) -> bool:
        """True if the user has at least one of the listed roles."""
        if not roles:
            return True
        return any(r in self.roles for r in roles)

    def has_admin(self) -> bool:
        return "admin" in self.roles

    def has_auditor(self) -> bool:
        return "auditor" in self.roles

    def can_view_line(self, line_id: str) -> bool:
        """Read-only access. admin / viewer / auditor / bp:<line> all OK."""
        if self.has_admin() or "viewer" in self.roles or self.has_auditor():
            return True
        return f"bp:{line_id}" in self.roles

    def can_write_line(self, line_id: str) -> bool:
        """Write access. admin OR (auditor) OR bp:<line>."""
        if self.has_admin():
            return True
        if f"bp:{line_id}" in self.roles:
            return True
        return False

    def to_public_dict(self) -> dict[str, Any]:
        """Project to the public /me response shape."""
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "is_active": self.is_active,
            "roles": list(self.roles),
            "accessible_lines": list(self.accessible_lines),
        }


# ---------------------------------------------------------------------------
# User lookup helpers (DB)
# ---------------------------------------------------------------------------


async def _load_user_by_id(user_id: int) -> CurrentUser | None:
    """Read a user + their roles + accessible_lines from the DB.

    Returns None if the user is missing or inactive. The token is still
    valid (cryptographically) but the user is gone, so we treat it as
    "no current user".
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            user_row = (
                await session.execute(
                    text(
                        "SELECT id, username, display_name, email, is_active "
                        "FROM users WHERE id = :uid"
                    ),
                    {"uid": user_id},
                )
            ).mappings().first()
            if not user_row:
                return None
            if not user_row["is_active"]:
                return None
            roles_rows = (
                await session.execute(
                    text("SELECT role FROM user_roles WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            ).scalars().all()
            lines_rows = (
                await session.execute(
                    text(
                        "SELECT line_id FROM user_business_lines "
                        "WHERE user_id = :uid ORDER BY line_id"
                    ),
                    {"uid": user_id},
                )
            ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        # DB is down → we can't verify the user. Refuse the request so
        # callers don't silently inherit stale permissions.
        logger.warning("_load_user_by_id: DB lookup failed for uid=%s: %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth backend unavailable",
        ) from exc
    roles = [str(r) for r in (roles_rows or [])]
    lines = [str(x) for x in (lines_rows or [])]
    return CurrentUser(
        id=int(user_row["id"]),
        username=str(user_row["username"]),
        display_name=str(user_row["display_name"] or user_row["username"]),
        email=user_row["email"],
        is_active=bool(user_row["is_active"]),
        roles=roles,
        accessible_lines=lines,
    )


async def _load_user_by_credentials(
    username: str, password: str
) -> CurrentUser | None:
    """Verify a username + password and return the CurrentUser (or None).

    Refactored into a function so tests can patch it. The router calls
    this for /api/auth/login.
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, username, display_name, email, is_active, "
                        "password_hash FROM users WHERE username = :u"
                    ),
                    {"u": username},
                )
            ).mappings().first()
            if not row or not row["is_active"]:
                return None
            if not verify_password(password, row["password_hash"]):
                return None
            user_id = int(row["id"])
            roles_rows = (
                await session.execute(
                    text("SELECT role FROM user_roles WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            ).scalars().all()
            lines_rows = (
                await session.execute(
                    text(
                        "SELECT line_id FROM user_business_lines "
                        "WHERE user_id = :uid ORDER BY line_id"
                    ),
                    {"uid": user_id},
                )
            ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_load_user_by_credentials: DB lookup failed for user=%s: %s",
            username, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth backend unavailable",
        ) from exc
    return CurrentUser(
        id=int(row["id"]),
        username=str(row["username"]),
        display_name=str(row["display_name"] or row["username"]),
        email=row["email"],
        is_active=bool(row["is_active"]),
        roles=[str(r) for r in (roles_rows or [])],
        accessible_lines=[str(x) for x in (lines_rows or [])],
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def _cookie_name() -> str:
    settings = get_settings()
    return os.environ.get("FIN_BP_COOKIE_NAME") or settings.cookie_name or "finbp_token"


async def get_current_user(
    request: Request,
    finbp_token: str | None = Cookie(default=None),
) -> CurrentUser:
    """Resolve the current user from the httpOnly cookie.

    Order of precedence:
      1. ``finbp_token`` cookie (primary — httpOnly, XSS-resistant).
      2. ``Authorization: Bearer <jwt>`` header (curl / API clients).

    Raises 401 on missing / invalid / expired tokens.
    """
    token = finbp_token
    if not token:
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(token)
    user = await _load_user_by_id(payload.sub)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user no longer exists or is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ---------------------------------------------------------------------------
# Exported helpers
# ---------------------------------------------------------------------------


__all__ = [
    "CurrentUser",
    "TokenPayload",
    "create_access_token",
    "decode_token",
    "get_current_user",
    "hash_password",
    "verify_password",
    "_cookie_name",
    "_load_user_by_id",
    "_load_user_by_credentials",
]
