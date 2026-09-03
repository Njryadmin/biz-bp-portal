"""
apps/api/app/core/rbac.py

Role-based access control dependencies.

Three flavors of guard, all built on top of ``get_current_user``:

* ``require_role(*roles)``    — has at least one of the listed roles.
* ``business_line_dep(...)``  — checks the ``line_id`` path parameter
                                and rejects if the user can't access that
                                specific line (read or write).
* ``require_admin_dep``       — convenience wrapper for admin-only
                                endpoints (user management, scraper
                                runs, upload).

These guards return the ``CurrentUser`` so the handler can use it. They
never ``return None`` — they always raise ``HTTPException(403)`` on
denial.
"""
from __future__ import annotations

from collections.abc import Iterable

from fastapi import Depends, HTTPException, Path, status

from .auth import CurrentUser, get_current_user
from .logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Role guards
# ---------------------------------------------------------------------------


def require_role(*allowed_roles: str):
    """Build a FastAPI dependency that accepts any of ``allowed_roles``.

    Usage::

        @router.post("/admin/reset")
        def reset_all(user: CurrentUser = Depends(require_role("admin"))):
            ...
    """
    allowed = tuple(allowed_roles)

    async def _inner(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not allowed:
            return user
        if user.has_role(*allowed):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"role required: one of {list(allowed)}; "
                f"user has {user.roles}"
            ),
        )

    return _inner


async def require_admin_dep(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Dependency: caller must have the ``admin`` role."""
    if not user.has_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return user


async def require_auditor_or_admin_dep(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Read-only access to audit log etc. — admin OR auditor."""
    if not (user.has_admin() or user.has_auditor()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin or auditor role required",
        )
    return user


# ---------------------------------------------------------------------------
# Business-line guard
# ---------------------------------------------------------------------------


async def require_business_line(
    line_id: str,
    user: CurrentUser,
    *,
    require_write: bool = False,
) -> CurrentUser:
    """Reject if ``user`` cannot access the given business line.

    ``require_write=True`` tightens the check to users with write
    access (admin OR bp:<line>).
    """
    if not line_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="line_id is required",
        )
    if require_write:
        if not user.can_write_line(line_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"no write access to business line '{line_id}'; "
                    f"user has roles={user.roles}"
                ),
            )
    else:
        if not user.can_view_line(line_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"no access to business line '{line_id}'; "
                    f"user has roles={user.roles}"
                ),
            )
    return user


def business_line_dep(*, require_write: bool = False):
    """Build a dependency that resolves the user + checks line access.

    Use as::

        @router.get("/lines/{line_id}/projects")
        def list_projects(
            user: CurrentUser = Depends(business_line_dep()),
        ): ...

    The dependency looks for a path parameter named ``line_id``.
    """
    async def _dep(
        line_id: str = Path(..., description="business line id"),
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        return await require_business_line(
            line_id, user, require_write=require_write
        )

    return _dep


def business_line_router_guard(line_id: str, *, require_write: bool = False):
    """Build a dependency suitable for ``include_router(..., dependencies=[...])``.

    Unlike ``business_line_dep``, this dep does NOT look at a path
    parameter — it is used to lock down an entire router that has been
    mounted under a known prefix like ``/api/lines/residential``.

    Usage::

        app.include_router(
            line_router,
            prefix="/api/lines/residential",
            dependencies=[Depends(business_line_router_guard("residential"))],
        )
    """
    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        return await require_business_line(
            line_id, user, require_write=require_write
        )

    return _dep


# ---------------------------------------------------------------------------
# Filter helpers (for endpoints that LIST things the user can see)
# ---------------------------------------------------------------------------


def filter_accessible_lines(
    user: CurrentUser, all_line_ids: Iterable[str]
) -> list[str]:
    """Return the subset of ``all_line_ids`` the user can view.

    Rules:
      * ``admin`` / ``auditor`` / ``viewer``  → see every line
      * ``bp:<line>``                          → only that line
      * multiple roles are unioned.
    """
    if user.has_admin() or user.has_auditor() or "viewer" in user.roles:
        return list(all_line_ids)
    allowed = set(user.accessible_lines or [])
    # bp:<line> roles also grant access to that line
    for r in user.roles:
        if r.startswith("bp:"):
            allowed.add(r[3:])
    return [lid for lid in all_line_ids if lid in allowed]


__all__ = [
    "filter_accessible_lines",
    "require_admin_dep",
    "require_auditor_or_admin_dep",
    "require_business_line",
    "business_line_dep",
    "require_role",
]
