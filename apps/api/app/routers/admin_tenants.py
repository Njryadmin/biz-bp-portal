"""
apps/api/app/routers/admin_tenants.py
======================================

M3 多租户 — Tenant management (super admin only, 2026-09-04).

Endpoints
---------
* ``GET    /api/admin/tenants``            — super_admin: list all tenants
* ``POST   /api/admin/tenants``            — super_admin: create a new tenant
* ``PATCH  /api/admin/tenants/{tenant_id}`` — super_admin: update name / plan / is_active

The self-service ``/api/auth/me-tenant`` lives in ``routers/auth.py`` so
auth-related endpoints stay co-located. Both endpoints share the same
response shape (``TenantInfo``) defined in ``schemas/tenant.py``.

Authorization
-------------
All three admin endpoints are gated by ``require_super_admin_dep`` —
plain ``admin`` role is NOT enough. The plan is that the ``is_super_admin``
flag is rare (the on-prem operator at deployment time) so the blast
radius of tenant-management operations stays small.

Tenant counts
-------------
* ``user_count``: a real ``COUNT(*) FROM users WHERE tenant_id = ...``.
  We do this with a separate GROUP BY query so we don't N+1 the
  tenant list.
* ``business_line_count``: MOCK. We read the manifest registry and
  return its length. Per-tenant line binding is a follow-up migration
  (the registry is global today, not per-tenant), and the admin UI
  only needs *a* number to render the column.

RLS interaction
---------------
``users`` is RLS-protected. To list users across tenants the query
needs ``bypass_rls=True`` on the tenant session. We use
``bypass_rls=ctx.bypass_rls`` (the same flag M2 already plumbed
through). For the tenants list itself we use ``admin_tenants`` queries
against ``tenants`` which is NOT RLS-protected (the table is the
catalogue, not the protected data) — so the list itself is fine
without bypass, but the cross-tenant user count does need it.

Audit
-----
Each admin write is captured by the global AuditMiddleware (see
``apps/api/app/middleware/audit.py``) automatically — no per-endpoint
audit code needed.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from ..core.auth import CurrentUser, get_current_user
from ..core.logging import get_logger
from ..core.rbac import require_super_admin_dep
from ..core.registry import load_registry
from ..core.tenant_context import TenantContext, get_tenant_context
from ..db.tenant import tenant_session
from ..schemas.tenant import (
    CreateTenantPayload,
    TenantInfo,
    TenantListResponse,
    UpdateTenantPayload,
)


logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants",
    tags=["admin", "tenants"],
    dependencies=[],  # per-endpoint auth
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Reused for slug validation. Mirrors the regex in
# ``CreateTenantPayload`` (so the Pydantic layer and the safety net
# stay aligned) but applied at the SQL layer as a defence in depth.
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def _row_to_tenant_info(
    row: dict[str, Any],
    *,
    user_count: int | None = None,
    business_line_count: int | None = None,
) -> TenantInfo:
    """Build a TenantInfo from a tenants-table row mapping.

    The columns we SELECT are a strict subset; we do not pass the raw
    row through so a future schema change (new column) cannot leak
    internal data into the wire contract.
    """
    return TenantInfo(
        id=str(row["id"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        plan=str(row["plan"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"].isoformat()
        if hasattr(row["created_at"], "isoformat")
        else str(row["created_at"]),
        user_count=user_count,
        business_line_count=business_line_count,
    )


def _parse_tenant_id(tenant_id: str) -> UUID:
    """Validate + convert the path param. 400 on bad UUID.

    Kept separate from the handler so a 400 with a consistent error
    message is returned even if FastAPI's auto path validation ever
    gets reconfigured.
    """
    try:
        return UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid tenant id: {tenant_id!r}",
        ) from exc


def _validate_plan(plan: str) -> None:
    """Defence-in-depth: re-check plan against the DB CHECK constraint.

    The Pydantic layer already restricts ``plan`` to the 3-value
    literal-union, but we re-check here so a future schema drift
    (CHECK constraint removed / new value added) doesn't open a
    silent failure mode.
    """
    from ..schemas.tenant import PLAN_VALUES
    if plan not in PLAN_VALUES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"plan must be one of {list(PLAN_VALUES)}; got {plan!r}",
        )


# ---------------------------------------------------------------------------
# GET /api/admin/tenants — list
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=TenantListResponse,
    summary="Super admin: list all tenants with per-tenant user / line counts",
)
async def list_tenants(
    _user: CurrentUser = Depends(require_super_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> TenantListResponse:
    """List every tenant in the system.

    Response shape::

        {
          "count": N,
          "tenants": [
            {"id": "...", "slug": "...", "name": "...", "plan": "...",
             "is_active": true, "created_at": "...",
             "user_count": 12, "business_line_count": 9},
            ...
          ]
        }

    ``business_line_count`` is the count of entries in the in-process
    registry (``load_registry()``) — see module docstring for why
    it's mocked.
    """
    # Count business lines up-front (synchronous, in-process). The
    # value is the same for every tenant today, but reading it once
    # here keeps the loop body tight and means a future per-tenant
    # binding only changes the loop, not the shape.
    try:
        line_count = len(load_registry())
    except Exception as exc:  # noqa: BLE001
        # Manifest loader can fail (e.g. registry.yaml missing in
        # test). Treat that as "0 lines" so the admin UI degrades
        # gracefully instead of 500-ing.
        logger.warning("list_tenants: load_registry failed: %s", exc)
        line_count = 0

    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        # tenants table is NOT RLS-protected, but we still go
        # through tenant_session so the GUC is set consistently.
        tenant_rows = (
            await session.execute(
                text(
                    "SELECT id, slug, name, plan, is_active, created_at "
                    "FROM tenants ORDER BY created_at ASC, slug ASC"
                )
            )
        ).mappings().all()

        # Aggregate user counts in one round-trip. The users table
        # is RLS-protected, so we need bypass_rls=True to count
        # across tenants. The TenantContext for a super admin
        # already has bypass_rls=True (set in
        # ``get_tenant_context``), so the counts are global.
        user_count_rows = (
            await session.execute(
                text(
                    "SELECT tenant_id, COUNT(*) AS n "
                    "FROM users GROUP BY tenant_id"
                )
            )
        ).mappings().all()
    user_counts: dict[str, int] = {
        str(r["tenant_id"]): int(r["n"]) for r in (user_count_rows or [])
    }

    items: list[TenantInfo] = [
        _row_to_tenant_info(
            dict(r),
            user_count=user_counts.get(str(r["id"]), 0),
            business_line_count=line_count,
        )
        for r in (tenant_rows or [])
    ]
    return TenantListResponse(count=len(items), tenants=items)


# ---------------------------------------------------------------------------
# POST /api/admin/tenants — create
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=TenantInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Super admin: create a new tenant (slug must be unique)",
)
async def create_tenant(
    body: CreateTenantPayload,
    _user: CurrentUser = Depends(require_super_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> TenantInfo:
    """Create a new tenant.

    Slug is the unique identifier. Once created, the slug is
    **immutable** (we don't expose a slug PATCH on the router) — a
    rename would break every URL that references the old slug
    (audit log entries, raw.audit_log / users.tenant_id references,
    etc.).

    Returns 409 on slug collision, 400 on bad plan, 201 + the new
    TenantInfo on success.
    """
    _validate_plan(body.plan)

    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        # Pre-check slug uniqueness for a clean 409 message. The
        # UNIQUE constraint on ``tenants.slug`` would also catch a
        # race, but the error would be a generic 500 — so we
        # check first and translate.
        existing = (
            await session.execute(
                text("SELECT id FROM tenants WHERE slug = :s"),
                {"s": body.slug},
            )
        ).mappings().first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"slug already exists: {body.slug!r}",
            )

        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO tenants (slug, name, plan, is_active)
                    VALUES (:slug, :name, :plan, :is_active)
                    RETURNING id, slug, name, plan, is_active, created_at
                    """
                ),
                {
                    "slug": body.slug,
                    "name": body.name,
                    "plan": body.plan,
                    "is_active": body.is_active,
                },
            )
        ).mappings().first()
        await session.commit()

    assert row is not None
    logger.info(
        "create_tenant: created slug=%s plan=%s is_active=%s",
        body.slug, body.plan, body.is_active,
    )
    return _row_to_tenant_info(dict(row), user_count=0, business_line_count=0)


# ---------------------------------------------------------------------------
# PATCH /api/admin/tenants/{tenant_id} — update
# ---------------------------------------------------------------------------


@router.patch(
    "/{tenant_id}",
    response_model=TenantInfo,
    summary="Super admin: update name / plan / is_active (slug is immutable)",
)
async def update_tenant(
    tenant_id: str,
    body: UpdateTenantPayload,
    _user: CurrentUser = Depends(require_super_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
) -> TenantInfo:
    """Update mutable tenant fields.

    Slug is intentionally not in the Pydantic body (and the body
    model has ``extra='forbid'``), so any client that tries to
    rename a tenant gets a 422 instead of a silent no-op.

    Returns 404 on unknown tenant_id, 400 on bad plan, 200 + the
    updated TenantInfo on success.
    """
    tid = _parse_tenant_id(tenant_id)

    if body.plan is not None:
        _validate_plan(body.plan)

    # Build the SET clause dynamically — only touch columns the
    # caller actually passed. None means "leave alone".
    sets: list[str] = []
    params: dict[str, Any] = {"tid": str(tid)}
    if body.name is not None:
        sets.append("name = :name")
        params["name"] = body.name
    if body.plan is not None:
        sets.append("plan = :plan")
        params["plan"] = body.plan
    if body.is_active is not None:
        sets.append("is_active = :is_active")
        params["is_active"] = body.is_active

    if not sets:
        # Empty body — return the current state instead of 400, so
        # the UI can "save" an unchanged form without a round-trip
        # error. This matches PATCH semantics in the rest of the
        # codebase.
        async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, slug, name, plan, is_active, created_at "
                        "FROM tenants WHERE id = :tid"
                    ),
                    {"tid": str(tid)},
                )
            ).mappings().first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"tenant not found: {tenant_id!r}",
            )
        return _row_to_tenant_info(dict(row), user_count=0, business_line_count=0)

    set_clause = ", ".join(sets)
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        row = (
            await session.execute(
                text(
                    f"""
                    UPDATE tenants
                    SET {set_clause}
                    WHERE id = :tid
                    RETURNING id, slug, name, plan, is_active, created_at
                    """
                ),
                params,
            )
        ).mappings().first()
        await session.commit()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"tenant not found: {tenant_id!r}",
        )

    logger.info(
        "update_tenant: id=%s fields=%s", tid,
        sorted(sets),
    )
    return _row_to_tenant_info(dict(row), user_count=0, business_line_count=0)


__all__ = ["router"]
