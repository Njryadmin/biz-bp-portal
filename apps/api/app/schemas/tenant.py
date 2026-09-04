"""
apps/api/app/schemas/tenant.py
================================

Pydantic request / response models for the M3 tenant admin endpoints
(commit 2026-09-04).

Why a dedicated module
----------------------
The tenant entities are managed via ``/api/admin/tenants/*`` (super
admin only) and the self-service ``/api/auth/me-tenant`` (any logged-in
user). All three endpoints share the same response shape — ``TenantInfo``
— so it lives here and is imported by both routers.

Wire contract
-------------
Mirrors ``packages/types/src/index.ts`` (TenantInfo / CreateTenantPayload /
UpdateTenantPayload / TenantListResponse). When adding a field, update
both sides at the same time or the BFF type-check will fail.

Validation rules
----------------
* ``slug``           : url-safe, ``^[a-z0-9-]+$``; immutable once created.
* ``name``           : 1-128 chars, no whitespace trimming (Pydantic v2 default).
* ``plan``           : one of ``standard`` / ``enterprise`` / ``demo``.
* ``is_active``      : boolean; default True on create.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Plan enum — kept here in one place so the validators + the
# documentation agree. Must match the ``tenants_plan_check`` CHECK
# constraint in ``infra/migrations/003_multi_tenant_setup.sql``.
PLAN_VALUES: tuple[str, ...] = ("standard", "enterprise", "demo")
PlanLiteral = Literal["standard", "enterprise", "demo"]


class TenantInfo(BaseModel):
    """Single tenant payload — used by all M3 endpoints.

    Fields
    ------
    id, slug, name, plan, is_active, created_at:
        Direct columns from the ``tenants`` table.
    user_count:
        Number of users currently in this tenant. Computed server-side
        at read time so the admin UI can show "Acme — 42 users"
        without a follow-up query. Optional in the wire contract so
        the ``/api/auth/me-tenant`` response can omit it (we don't
        need a user count to display the current tenant's name).
    business_line_count:
        Number of business lines the tenant has access to. Mock for
        now (counts every line in the registry — every tenant sees
        the same lines until we ship per-tenant line access in a
        later commit). The mock is intentional so the UI has a
        column to render; the real per-tenant binding lives in a
        follow-up migration.
    """

    id: str = Field(..., description="UUID, primary key")
    slug: str = Field(..., description="url-safe slug, e.g. 'acme-realty'")
    name: str = Field(..., description="display name, e.g. 'Acme Realty'")
    plan: PlanLiteral = Field(..., description="billing plan")
    is_active: bool = Field(..., description="True iff tenant is enabled")
    created_at: str = Field(..., description="ISO-8601 timestamp (server-side)")
    user_count: Optional[int] = Field(
        default=None,
        description="users in this tenant (admin endpoints only)",
    )
    business_line_count: Optional[int] = Field(
        default=None,
        description="business lines accessible to this tenant "
        "(admin endpoints only, mocked)",
    )


class CreateTenantPayload(BaseModel):
    """POST /api/admin/tenants body."""

    slug: str = Field(
        ...,
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9-]+$",
        description="url-safe slug, ^[a-z0-9-]+$; immutable after create",
    )
    name: str = Field(..., min_length=1, max_length=128, description="display name")
    plan: PlanLiteral = Field(
        default="standard",
        description="billing plan; one of standard / enterprise / demo",
    )
    is_active: bool = Field(
        default=True,
        description="True to enable the tenant on creation; default True",
    )

    @field_validator("slug")
    @classmethod
    def _slug_lowercase_only(cls, v: str) -> str:
        # The regex pattern enforces shape; this validator exists so
        # Pydantic emits a clearer error message on mismatch.
        if v != v.lower():
            raise ValueError("slug must be lowercase")
        if v.startswith("-") or v.endswith("-"):
            raise ValueError("slug must not start or end with '-'")
        if "--" in v:
            raise ValueError("slug must not contain consecutive '-'")
        return v


class UpdateTenantPayload(BaseModel):
    """PATCH /api/admin/tenants/{id} body. Every field optional.

    ``slug`` is intentionally NOT in this model — slugs are immutable
    identifiers, and the admin UI never renames them. The Pydantic
    ``extra='forbid'`` mode (configured below) means a stray ``slug``
    key in the request body will be rejected with a 422.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    plan: Optional[PlanLiteral] = Field(default=None)
    is_active: Optional[bool] = Field(default=None)

    model_config = {"extra": "forbid"}


class TenantListResponse(BaseModel):
    """GET /api/admin/tenants response envelope."""

    count: int
    tenants: list[TenantInfo]


__all__ = [
    "CreateTenantPayload",
    "PLAN_VALUES",
    "PlanLiteral",
    "TenantInfo",
    "TenantListResponse",
    "UpdateTenantPayload",
]
