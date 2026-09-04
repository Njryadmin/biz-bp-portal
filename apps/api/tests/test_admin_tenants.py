"""
apps/api/tests/test_admin_tenants.py
====================================

M3 多租户 — super admin tenant management (2026-09-04).

Endpoints
---------
* GET    /api/admin/tenants             — list (super_admin only)
* POST   /api/admin/tenants             — create (super_admin only)
* PATCH  /api/admin/tenants/{tenant_id} — update (super_admin only)
* GET    /api/auth/me-tenant            — any logged-in user

We cover
--------
1. list requires super_admin (admin → 403)
2. list as super_admin returns default tenant
3. create slug must be unique (409 on collision)
4. create with invalid slug → 400
5. PATCH slug is immutable (body rejected, slug unchanged)
6. GET /me-tenant returns the current user's tenant
7. RLS isolation: super admin with X-Tenant-ID sees only that
   tenant's users (re-uses the M2 isolation contract)

Why we hit the real pgserver
----------------------------
Same as ``test_multi_tenant_m1.py`` / ``test_tenant_context_m2.py``:
the tenants table + RLS are SQL features, not Python. Mocking them
would test nothing.

Run with::

    cd apps/api
    BIZ_BP_DATABASE_URL=postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp \\
        python -m pytest tests/test_admin_tenants.py -v --tb=short
"""
from __future__ import annotations

import asyncio
import os
import socket
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse
from uuid import UUID

import pytest
from fastapi import Cookie, Request
from fastapi.testclient import TestClient
from sqlalchemy import text


os.environ.setdefault(
    "JWT_SECRET", "test-jwt-secret-for-m3-tenant-admin-not-for-production"
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# Fixtures: pgserver gate + connection helpers
# ---------------------------------------------------------------------------


def _parse_pg_dsn() -> dict[str, object]:
    from app.core.config import get_settings
    url = get_settings().database_url.replace("+asyncpg", "")
    u = urlparse(url)
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 5432,
        "user": u.username,
        "password": u.password or "",
        "database": (u.path or "/postgres").lstrip("/") or "postgres",
    }


@pytest.fixture(scope="module")
def postgres_available_m3():
    cfg = _parse_pg_dsn()
    try:
        with socket.create_connection((cfg["host"], cfg["port"]), timeout=0.5):
            return cfg
    except (OSError, socket.timeout):
        pytest.skip(
            f"Postgres not reachable at {cfg['host']}:{cfg['port']} — "
            f"M3 tenant admin tests skipped"
        )


def _reset_engine_safe() -> None:
    try:
        from app.db import session as session_mod
        session_mod.reset_engine()
    except Exception:  # noqa: BLE001
        pass


def _run_async(coro):
    _reset_engine_safe()
    try:
        return asyncio.run(coro)
    finally:
        _reset_engine_safe()


def _create_test_tenant(slug: str, name: str) -> UUID:
    async def _do() -> UUID:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO tenants (slug, name, plan, is_active) "
                        "VALUES (:s, :n, 'standard', TRUE) RETURNING id"
                    ),
                    {"s": slug, "n": name},
                )
            ).mappings().first()
            await session.commit()
        return UUID(str(row["id"]))
    return _run_async(_do())


def _delete_tenant(tenant_id: UUID) -> None:
    async def _do() -> None:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text("DELETE FROM tenants WHERE id = :tid"),
                {"tid": str(tenant_id)},
            )
            await session.commit()
    _run_async(_do())


def _tenant_exists(slug: str) -> bool:
    async def _do() -> bool:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text("SELECT 1 FROM tenants WHERE slug = :s"),
                    {"s": slug},
                )
            ).first()
        return row is not None
    return _run_async(_do())


def _upsert_super_admin() -> None:
    async def _do() -> None:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE users SET is_super_admin = TRUE "
                    "WHERE username = 'admin' AND is_super_admin = FALSE"
                )
            )
            await session.commit()
    _run_async(_do())


# ---------------------------------------------------------------------------
# TestClient fixtures
# ---------------------------------------------------------------------------


@contextmanager
def _super_admin_client() -> Iterator[TestClient]:
    """Build a TestClient with ``get_current_user`` overridden to a
    super admin. We override the v1 dep (not the
    ``require_super_admin_dep``) so the layout's request.state
    write happens, matching the production request lifecycle.
    """
    from app.main import create_app
    from app.core.auth import CurrentUser, get_current_user
    from app.db import session as session_mod

    _upsert_super_admin()
    app = create_app()
    user = CurrentUser(
        id=1,
        username="admin",
        display_name="Test Super Admin",
        email="admin@test.local",
        is_active=True,
        roles=["admin"],
        accessible_lines=[],
        is_super_admin=True,
        tenant_id=str(DEFAULT_TENANT_ID),
    )

    async def _override(
        request: Request,
        finbp_token: str | None = Cookie(default=None),
    ) -> CurrentUser:
        request.state.current_user = user
        return user

    app.dependency_overrides[get_current_user] = _override
    session_mod.reset_engine()
    with TestClient(app) as c:
        yield c
    session_mod.reset_engine()


@contextmanager
def _plain_admin_client() -> Iterator[TestClient]:
    """A user with the ``admin`` role but ``is_super_admin=False``.
    Used to verify ``require_super_admin_dep`` rejects the legacy
    admin role (the M2 split).
    """
    from app.main import create_app
    from app.core.auth import CurrentUser, get_current_user
    from app.db import session as session_mod

    app = create_app()
    user = CurrentUser(
        id=2,
        username="plain-admin",
        display_name="Plain Admin",
        email="plain-admin@test.local",
        is_active=True,
        roles=["admin"],
        accessible_lines=[],
        is_super_admin=False,  # ← 关键: 角色是 admin 但不是 super admin
        tenant_id=str(DEFAULT_TENANT_ID),
    )

    async def _override(
        request: Request,
        finbp_token: str | None = Cookie(default=None),
    ) -> CurrentUser:
        request.state.current_user = user
        return user

    app.dependency_overrides[get_current_user] = _override
    session_mod.reset_engine()
    with TestClient(app) as c:
        yield c
    session_mod.reset_engine()


@contextmanager
def _normal_user_client(tenant_id: UUID) -> Iterator[TestClient]:
    """A non-admin, non-super-admin user bound to ``tenant_id``."""
    from app.main import create_app
    from app.core.auth import CurrentUser, get_current_user
    from app.db import session as session_mod

    app = create_app()
    user = CurrentUser(
        id=999,
        username="m3-normal",
        display_name="M3 Normal",
        email="m3-normal@test.local",
        is_active=True,
        roles=["viewer"],  # some role so deps don't 403 for trivial reasons
        accessible_lines=[],
        is_super_admin=False,
        tenant_id=str(tenant_id),
    )

    async def _override(
        request: Request,
        finbp_token: str | None = Cookie(default=None),
    ) -> CurrentUser:
        request.state.current_user = user
        return user

    app.dependency_overrides[get_current_user] = _override
    session_mod.reset_engine()
    with TestClient(app) as c:
        yield c
    session_mod.reset_engine()


# ---------------------------------------------------------------------------
# 1) list requires super_admin — plain admin gets 403
# ---------------------------------------------------------------------------


def test_list_tenants_requires_super_admin(postgres_available_m3):
    """admin (但 is_super_admin=False) 调 GET /api/admin/tenants → 403."""
    with _plain_admin_client() as c:
        r = c.get("/api/admin/tenants")
    assert r.status_code == 403, r.text
    assert "super_admin" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 2) list as super_admin returns the default tenant
# ---------------------------------------------------------------------------


def test_list_tenants_as_super_admin(postgres_available_m3):
    """super_admin 调 GET → 200, 至少含 default tenant."""
    with _super_admin_client() as c:
        r = c.get("/api/admin/tenants")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == len(body["tenants"])
    slugs = {t["slug"] for t in body["tenants"]}
    assert "default" in slugs, f"default tenant missing: {slugs}"
    # The default tenant's user_count is > 0 (we have admin + 8 BP
    # users bound to it from M1's backfill).
    default_row = next(t for t in body["tenants"] if t["slug"] == "default")
    assert default_row["user_count"] >= 9
    # plan must be in the v1 set
    assert default_row["plan"] in ("standard", "enterprise", "demo")


# ---------------------------------------------------------------------------
# 3) create slug must be unique → 409
# ---------------------------------------------------------------------------


def test_create_tenant_unique_slug(postgres_available_m3):
    slug = f"m3-test-uniq-{os.getpid()}"
    payload = {"slug": slug, "name": "M3 Test Uniq"}
    try:
        with _super_admin_client() as c:
            r1 = c.post("/api/admin/tenants", json=payload)
        assert r1.status_code == 201, r1.text
        body1 = r1.json()
        assert body1["slug"] == slug

        with _super_admin_client() as c:
            r2 = c.post("/api/admin/tenants", json=payload)
        assert r2.status_code == 409, r2.text
        assert slug in r2.json()["detail"]
    finally:
        if _tenant_exists(slug):
            tid = _run_async(_get_tenant_id_by_slug(slug))
            _delete_tenant(tid)


async def _get_tenant_id_by_slug(slug: str) -> UUID:
    from app.db.session import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT id FROM tenants WHERE slug = :s"),
                {"s": slug},
            )
        ).mappings().first()
    assert row is not None
    return UUID(str(row["id"]))


# ---------------------------------------------------------------------------
# 4) create with invalid slug (uppercase) → 422
# ---------------------------------------------------------------------------


def test_create_tenant_invalid_slug(postgres_available_m3):
    """slug 含大写字母 → Pydantic 422 (Pydantic 先于 router 拒绝)."""
    with _super_admin_client() as c:
        r = c.post(
            "/api/admin/tenants",
            json={"slug": "BadSlug-With-Caps", "name": "M3 Bad Slug"},
        )
    assert r.status_code == 422, r.text
    # Pydantic's detail should mention the pattern violation
    detail = r.json().get("detail", [])
    assert isinstance(detail, list)
    assert any("slug" in str(d.get("loc", [])).lower() for d in detail)


def test_create_tenant_invalid_slug_with_space(postgres_available_m3):
    """slug 含空格 → 422 (Pydantic pattern ^[a-z0-9-]+$)."""
    with _super_admin_client() as c:
        r = c.post(
            "/api/admin/tenants",
            json={"slug": "has space", "name": "M3 Bad Slug Space"},
        )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 5) PATCH slug is immutable — unknown field rejected with 422
# ---------------------------------------------------------------------------


def test_patch_tenant_slug_immutable(postgres_available_m3):
    """PATCH body 含 slug → 422 (UpdateTenantPayload extra='forbid')."""
    slug = f"m3-test-immut-{os.getpid()}"
    tid: UUID | None = None
    try:
        # Create a tenant first.
        with _super_admin_client() as c:
            r = c.post(
                "/api/admin/tenants",
                json={"slug": slug, "name": "M3 Immutable"},
            )
        assert r.status_code == 201, r.text
        tid = UUID(r.json()["id"])

        # Try to PATCH slug → must be rejected, slug must not change.
        with _super_admin_client() as c:
            r2 = c.patch(
                f"/api/admin/tenants/{tid}",
                json={"slug": "totally-different-slug", "name": "Renamed"},
            )
        assert r2.status_code == 422, r2.text
        # Verify the on-disk slug is unchanged.
        assert _tenant_exists(slug)
    finally:
        if tid is not None:
            _delete_tenant(tid)


# ---------------------------------------------------------------------------
# 6) GET /me-tenant returns the current user's tenant
# ---------------------------------------------------------------------------


def test_get_me_tenant_returns_own_tenant(postgres_available_m3):
    """普通用户调 /api/auth/me-tenant → 200, 拿到自己绑定的 tenant."""
    # Create a test tenant, then bind a non-admin user to it.
    new_tenant = _create_test_tenant(
        slug=f"m3-me-tenant-{os.getpid()}",
        name="M3 Me Tenant",
    )
    try:
        with _normal_user_client(new_tenant) as c:
            r = c.get("/api/auth/me-tenant")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == str(new_tenant)
        assert body["slug"].startswith("m3-me-tenant-")
        # is_super_admin should be false for this non-admin user
        assert body["is_super_admin"] is False
    finally:
        _delete_tenant(new_tenant)


def test_get_me_tenant_super_admin(postgres_available_m3):
    """super admin 调 /me-tenant → 200, is_super_admin=True."""
    with _super_admin_client() as c:
        r = c.get("/api/auth/me-tenant")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_super_admin"] is True


# ---------------------------------------------------------------------------
# 7) RLS isolation via X-Tenant-ID — super admin sees only the target tenant
# ---------------------------------------------------------------------------


def test_super_admin_x_tenant_id_sees_target_tenant_only(postgres_available_m3):
    """super admin + X-Tenant-ID=tenant_b → /me-tenant 报 tenant_b.

    M2 的 RLS + M3 的 X-Tenant-ID 共同保证:super admin 用 header 切
    租户时,所有 RLS 受限的查询都走 header 指向的 tenant,而不是
    user.tenant_id.
    """
    new_tenant = _create_test_tenant(
        slug=f"m3-iso-{os.getpid()}",
        name="M3 Iso Tenant",
    )
    try:
        with _super_admin_client() as c:
            r = c.get(
                "/api/auth/me-tenant",
                headers={"X-Tenant-ID": str(new_tenant)},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        # When the super admin provides X-Tenant-ID, the response
        # reflects that tenant, not their user.tenant_id (default).
        assert body["id"] == str(new_tenant)
        assert body["slug"].startswith("m3-iso-")
    finally:
        _delete_tenant(new_tenant)


# ---------------------------------------------------------------------------
# 8) POST /api/admin/tenants — happy path round-trip
# ---------------------------------------------------------------------------


def test_create_tenant_happy_path(postgres_available_m3):
    slug = f"m3-happy-{os.getpid()}"
    payload = {
        "slug": slug,
        "name": "M3 Happy Path",
        "plan": "demo",
        "is_active": True,
    }
    try:
        with _super_admin_client() as c:
            r = c.post("/api/admin/tenants", json=payload)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["slug"] == slug
        assert body["name"] == "M3 Happy Path"
        assert body["plan"] == "demo"
        assert body["is_active"] is True
        assert body["user_count"] == 0
        assert body["business_line_count"] is not None
    finally:
        if _tenant_exists(slug):
            tid = _run_async(_get_tenant_id_by_slug(slug))
            _delete_tenant(tid)


# ---------------------------------------------------------------------------
# 9) PATCH happy path — name + plan + is_active
# ---------------------------------------------------------------------------


def test_patch_tenant_updates_mutable_fields(postgres_available_m3):
    slug = f"m3-patch-{os.getpid()}"
    tid: UUID | None = None
    try:
        with _super_admin_client() as c:
            r = c.post(
                "/api/admin/tenants",
                json={"slug": slug, "name": "Before", "plan": "standard"},
            )
        assert r.status_code == 201, r.text
        tid = UUID(r.json()["id"])

        with _super_admin_client() as c:
            r2 = c.patch(
                f"/api/admin/tenants/{tid}",
                json={"name": "After", "plan": "enterprise", "is_active": False},
            )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["name"] == "After"
        assert body["plan"] == "enterprise"
        assert body["is_active"] is False
        # slug must remain unchanged
        assert body["slug"] == slug
    finally:
        if tid is not None:
            _delete_tenant(tid)
