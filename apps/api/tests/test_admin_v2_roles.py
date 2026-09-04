"""
apps/api/tests/test_admin_v2_roles.py

================================================================
v2 RBAC admin endpoint 娴嬭瘯 (C1 浠诲姟, commit 2026-09-04)
================================================================

鑳屾櫙
----
v1 ``PATCH /api/auth/users/{id}/roles`` 鍙敮鎸?``roles: list[str]``
(``["admin", "bp:residential"]``), 涓嶈兘琛ㄨ揪 v2 鐭╅樀閲?``fin_bp`` + ``scope=business_line`` + ``line_id=residential`` 涓夊厓缁勩€?鏈祴璇曡鐩?C1 鏂板姞鐨勪袱涓鐐?:

    GET   /api/auth/users/{id}/v2-roles
    PATCH /api/auth/users/{id}/v2-roles

璁捐
----
1. **渚濊禆 override**: v2-roles 绔偣鐢?``Depends(require_admin_dep)``,
   涓嶈蛋 cookie 楠岃瘉. 鏈祴璇曠敤 ``app.dependency_overrides[require_admin_dep]``
   鐩存帴娉ㄥ叆 admin mock.

2. **鐪熷疄 DB (pgserver)**: 璺熺幇鏈?v1 admin 娴嬭瘯涓€鑷寸敤鐪熷疄 PG.
   ``postgres_available_v2`` fixture 鍦ㄦ棤 DB 鏃?skip 鏁存枃浠?

3. **鍗?event loop 妯″紡**: 鍥犱负 SQLAlchemy 寮傛 engine 缁戝畾鍒板垱寤哄畠鐨?   event loop, 鎴戜滑蹇呴』璁?setup 鍜?TestClient 鍏变韩鍚屼竴涓?loop.
   瀹炵幇: setup 璺戝湪 ``TestClient(app).__enter__()`` 涔嬪悗 鈥?姝ゆ椂
   TestClient 宸茬粡鍒涘缓浜?lifespan 鍐呯殑 event loop, 鎴戜滑鐨?setup 鍗忕▼
   鐢?``asyncio.run_coroutine_threadsafe`` 鍦ㄩ偅涓?loop 涓婅窇, 璺?   TestClient 瀹屽叏鍏变韩 engine. 绠€鍖栫増: 涓嶇洿鎺ヨ繘 setup, 鑰屾槸鍦?   TestClient 鍐呴儴鎶?setup 鐢?startup event 瑙﹀彂.

   閫€鑰屾眰鍏舵鐨勬柟妗? 鐢ㄥ悓姝ユ柟寮忔瀯閫犳祴璇曟暟鎹? 鐜版湁 test_auth.py
   閫氳繃 ``monkeypatch.setattr`` 鏇挎崲 ``_load_user_by_id`` 绛夊嚱鏁版潵
   閬垮紑 DB. 鎴戜滑鐨勬祴璇曢渶瑕佺湡瀹?DB 鍐? 鎵€浠ヨ蛋 setup 鍗忕▼杩欐潯璺?

   鏈€缁堟柟妗? setup 鐢ㄧ嫭绔?``asyncio.run()`` 鍦ㄧ嫭绔?loop 涓婅窇 (鍐?   user + user_roles), 鐒跺悗 ``reset_engine()`` 涓㈠純璇?loop 缁戝畾鐨?   engine, 璁?TestClient lifespan 鑷繁鏂板缓涓€涓?engine. TestClient
   鐨勬柊 engine 杩炴帴鍒板悓涓€涓?DB 瀹炰緥, 鐪嬪埌 setup 鍐欏叆鐨勬暟鎹?

4. **娓呯悊**: 妯″潡绾?``_TRACKED_USERNAMES`` + _admin_client 閫€鍑烘椂
   鑷姩 DELETE, 鍔犱笂姣忎釜娴嬭瘯鑷繁 try/finally 鍏滃簳.

鎵ц
----
    cd apps/api
    BIZ_BP_DATABASE_URL=postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp \\
        python -m pytest tests/test_admin_v2_roles.py -v --tb=short

鍙傝€?----
    apps/api/app/schemas/auth.py            UserRoleBindingResponse
    apps/api/app/routers/auth.py            get_user_v2_roles / update_user_v2_roles
    apps/api/app/core/rbac_v2.py            8 roles 脳 2 scopes 鏋氫妇
    infra/migrations/001_rbac_v2.sql       user_roles v2 schema
"""
from __future__ import annotations

import asyncio
import os
import socket
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


os.environ.setdefault(
    "JWT_SECRET", "test-jwt-secret-for-rbac-tests-not-for-production"
)


# M2 (2026-09-04): 多租户 — 显式带 tenant_id 走 DEFAULT_TENANT_ID.
# 测试 helper 直接写默认值, 不依赖 trigger set_tenant_from_guc() 的 fallback.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000000"


# ---------------------------------------------------------------------------
# DB 鍙揪鎬?+ connection helper
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
def postgres_available_v2():
    """pgserver 鍙揪鎬?gate. 涓嶅彲杈炬椂鏁存枃浠?skip."""
    cfg = _parse_pg_dsn()
    try:
        with socket.create_connection((cfg["host"], cfg["port"]), timeout=0.5):
            return cfg
    except (OSError, socket.timeout):
        pytest.skip(
            f"Postgres not reachable at {cfg['host']}:{cfg['port']} 鈥?"
            f"v2-roles tests skipped"
        )


# ---------------------------------------------------------------------------
# TestClient fixtures: 鎶?setup/cleanup 璺?TestClient lifespan 闅旂
# ---------------------------------------------------------------------------


@contextmanager
def _admin_client() -> Iterator[TestClient]:
    """Build a TestClient with ``require_admin_dep`` overridden to admin.

    Critical ordering: ``reset_engine()`` BEFORE entering TestClient so
    the lifespan's engine is bound to TestClient's loop (not the
    pytest main thread's loop). Cleanup is best-effort and runs in a
    fresh event loop via ``asyncio.run``.
    """
    from app.main import create_app
    from app.core.auth import CurrentUser
    from app.core.rbac import require_admin_dep
    from app.db import session as session_mod

    app = create_app()
    admin_user = CurrentUser(
        id=1,
        username="admin",
        display_name="Test Admin",
        email="admin@test.local",
        is_active=True,
        roles=["admin"],
        accessible_lines=[],
    )
    app.dependency_overrides[require_admin_dep] = lambda: admin_user
    # Reset cached engine so the lifespan builds a fresh one bound to
    # TestClient's event loop.
    session_mod.reset_engine()
    with TestClient(app) as c:
        yield c
    session_mod.reset_engine()
    # NB: we deliberately do NOT call _cleanup_tracked_users() here.
    # The autouse pytest fixture (see _cleanup_between_tests below)
    # handles per-test cleanup BEFORE the next test runs, so the
    # current test can still read the post-PATCH DB state with
    # _user_roles() / _user_lines() after the ``with`` block exits.


@contextmanager
def _nonadmin_client() -> Iterator[TestClient]:
    """Build a TestClient that fails ``require_admin_dep`` with 403."""
    from fastapi import HTTPException, status
    from app.main import create_app
    from app.core.rbac import require_admin_dep
    from app.db import session as session_mod

    app = create_app()

    async def _failing_dep():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )

    app.dependency_overrides[require_admin_dep] = _failing_dep
    session_mod.reset_engine()
    with TestClient(app) as c:
        yield c
    session_mod.reset_engine()


# ---------------------------------------------------------------------------
# DB helpers 鈥?all run in a fresh ``asyncio.run()`` loop, which creates
# its own engine via get_session_factory(). The data is written to the
# real DB, so the TestClient (with its own engine) can read it back.
# ---------------------------------------------------------------------------


def _reset_engine_safe() -> None:
    """Drop the cached engine so the next call rebuilds a fresh one."""
    try:
        from app.db import session as session_mod
        session_mod.reset_engine()
    except Exception:  # noqa: BLE001
        pass


def _run_async(coro):
    """Run a coroutine in a fresh event loop. Resets engine around it."""
    _reset_engine_safe()
    try:
        return asyncio.run(coro)
    finally:
        _reset_engine_safe()


def _ensure_migration_applied() -> None:
    """Confirm user_roles has the v2 scope + line_id columns."""
    async def _do() -> None:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text("ALTER TABLE user_roles ADD COLUMN IF NOT EXISTS scope TEXT")
            )
            await session.execute(
                text("ALTER TABLE user_roles ADD COLUMN IF NOT EXISTS line_id TEXT")
            )
            await session.commit()
    _run_async(_do())


def _create_user(username: str, *, password: str = "pw1234567") -> int:
    async def _do() -> int:
        from app.core.auth import hash_password
        from app.db.session import get_session_factory
        pwd_hash = hash_password(password)
        factory = get_session_factory()
        async with factory() as session:
            uid = (await session.execute(
                text(
                    # M2: 多租户 — INSERT 显式带 tenant_id.
                    # 不依赖 trigger set_tenant_from_guc() (那个是 router
                    # 走 tenant_session() 时的兜底; test helper 直接写
                    # DEFAULT_TENANT_ID 更明确, 也不会因 GUC 没设触发
                    # 触发器 fallback 路径造成混淆).
                    """
                    INSERT INTO users (username, display_name, email, password_hash, is_active, tenant_id)
                    VALUES (:u, :u, :e, :h, TRUE, :tid)
                    RETURNING id
                    """
                ),
                {
                    "u": username,
                    "e": f"{username}@test.local",
                    "h": pwd_hash,
                    "tid": DEFAULT_TENANT_ID,
                },
            )).scalar_one()
            await session.commit()
        return int(uid)
    return _run_async(_do())


def _create_admin_user(username: str = "admin", *, password: str = "pw1234567") -> int:
    """Create a user and grant them the global admin role.

    Convenience helper for tests that need at least one admin in the
    DB (e.g. so the last-admin protection can find a peer admin to
    hand the role over to). Without this helper, every test would
    have to remember to call ``_add_role`` after ``_create_user``,
    and a single forgotten call would mean the test's only admin is
    the one being demoted (triggering an unexpected 409).
    """
    uid = _create_user(username, password=password)
    _add_role(uid, "admin", "global", None)
    return uid


def _add_role(
    user_id: int,
    role: str,
    scope: str | None,
    line_id: str | None,
) -> None:
    async def _do() -> None:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                # M2: 多租户 — INSERT 显式带 tenant_id (走 DEFAULT_TENANT_ID).
                text(
                    "INSERT INTO user_roles (user_id, role, scope, line_id, tenant_id) "
                    "VALUES (:uid, :role, :scope, :line_id, :tid)"
                ),
                {
                    "uid": user_id,
                    "role": role,
                    "scope": scope,
                    "line_id": line_id,
                    "tid": DEFAULT_TENANT_ID,
                },
            )
            await session.commit()
    _run_async(_do())


def _user_roles(user_id: int) -> list[tuple]:
    async def _do() -> list[tuple]:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                text(
                    "SELECT role, scope, line_id FROM user_roles "
                    "WHERE user_id = :uid ORDER BY role, line_id"
                ),
                {"uid": user_id},
            )).all()
        return [(r[0], r[1], r[2]) for r in rows]
    return _run_async(_do())


def _user_lines(user_id: int) -> list[str]:
    async def _do() -> list[str]:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                text(
                    "SELECT line_id FROM user_business_lines "
                    "WHERE user_id = :uid ORDER BY line_id"
                ),
                {"uid": user_id},
            )).all()
        return [r[0] for r in rows]
    return _run_async(_do())


def _delete_user(user_id: int) -> None:
    async def _do() -> None:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text("DELETE FROM users WHERE id = :uid"), {"uid": user_id}
            )
            await session.commit()
    _run_async(_do())


def _admin_user_ids() -> list[int]:
    async def _do() -> list[int]:
        from app.db.session import get_session_factory
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                text("SELECT user_id FROM user_roles WHERE role = 'admin'")
            )).all()
        return sorted({r[0] for r in rows})
    return _run_async(_do())


# 璺熻釜鎵€鏈夊彲鑳借娴嬭瘯鍒涘缓鐨勭敤鎴? _admin_client 閫€鍑烘椂缁熶竴娓呯悊.
_TRACKED_USERNAMES: set[str] = set(
    {
        "admin", "alice", "bob", "carol", "dave", "eve", "frank",
        "grace", "henry", "iris", "jack", "kate", "leo",
        "admin-one", "admin-two", "lonely-admin",
    }
)


def _cleanup_tracked_users() -> None:
    """Best-effort DELETE for any tracked test user still in the DB."""
    if not _TRACKED_USERNAMES:
        return
    try:
        _reset_engine_safe()
        async def _do() -> None:
            from app.db.session import get_session_factory
            factory = get_session_factory()
            async with factory() as session:
                placeholders = ", ".join(
                    f":u{i}" for i in range(len(_TRACKED_USERNAMES))
                )
                await session.execute(
                    text(
                        f"DELETE FROM users WHERE username IN ({placeholders})"
                    ),
                    {f"u{i}": name for i, name in enumerate(_TRACKED_USERNAMES)},
                )
                await session.commit()
        asyncio.run(_do())
    except Exception:  # noqa: BLE001
        pass
    finally:
        _reset_engine_safe()


@pytest.fixture(autouse=True)
def _cleanup_between_tests():
    """autouse fixture: cleanup tracked users BEFORE and AFTER every test.

    Before: ensures a clean slate so a test that failed mid-way last
    time doesn't leave users around to break this test.
    After: catches any user the test forgot to delete in its finally
    block (e.g. if an AssertionError fired before the try/finally).
    """
    _cleanup_tracked_users()
    yield
    _cleanup_tracked_users()


# ---------------------------------------------------------------------------
# 1) Happy path: GET returns the v2 bindings
# ---------------------------------------------------------------------------


def test_get_v2_roles_happy_path(postgres_available_v2):
    """admin reads a user's v2 bindings; returns 200 with the full set."""
    _ensure_migration_applied()
    _create_admin_user()
    alice_id = _create_user("alice")
    _add_role(alice_id, "admin", "global", None)
    _add_role(alice_id, "fin_bp", "business_line", "residential")
    _add_role(alice_id, "hr_bp_global", "global", None)
    try:
        with _admin_client() as c:
            r = c.get(f"/api/auth/users/{alice_id}/v2-roles")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_id"] == alice_id
        roles = {b["role"] for b in body["bindings"]}
        assert roles == {"admin", "fin_bp", "hr_bp_global"}
        by_role = {b["role"]: b for b in body["bindings"]}
        assert by_role["admin"]["scope"] == "global"
        assert by_role["admin"]["line_id"] is None
        assert by_role["fin_bp"]["scope"] == "business_line"
        assert by_role["fin_bp"]["line_id"] == "residential"
        assert by_role["hr_bp_global"]["scope"] == "global"
        assert by_role["hr_bp_global"]["line_id"] is None
    finally:
        _delete_user(alice_id)


# ---------------------------------------------------------------------------
# 2) Happy path: PATCH replaces bindings
# ---------------------------------------------------------------------------


def test_patch_v2_roles_happy_path(postgres_available_v2):
    """admin PATCHes a new binding set; DB is rewritten; response echoes it."""
    _ensure_migration_applied()
    admin_id = _create_admin_user()
    bob_id = _create_user("bob")
    _add_role(bob_id, "admin", "global", None)
    new_bindings = [
        {"role": "auditor", "scope": "global", "line_id": None},
        {"role": "fin_bp", "scope": "business_line", "line_id": "residential"},
        {"role": "hr_bp", "scope": "business_line", "line_id": "retail"},
    ]
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{bob_id}/v2-roles",
                json={"bindings": new_bindings},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_id"] == bob_id
        assert len(body["bindings"]) == 3
        rows = _user_roles(bob_id)
        assert ("auditor", "global", None) in rows
        assert ("fin_bp", "business_line", "residential") in rows
        assert ("hr_bp", "business_line", "retail") in rows
        # replacement, not merge
        assert not any(role == "admin" for role, _, _ in rows)
    finally:
        _delete_user(bob_id)
        _delete_user(admin_id)


# ---------------------------------------------------------------------------
# 3) Business rule: scope=business_line without line_id 鈫?400
# ---------------------------------------------------------------------------


def test_patch_business_line_scope_requires_line_id(postgres_available_v2):
    _ensure_migration_applied()
    _create_admin_user()
    carol_id = _create_user("carol")
    _add_role(carol_id, "admin", "global", None)
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{carol_id}/v2-roles",
                json={
                    "bindings": [
                        {"role": "fin_bp", "scope": "business_line", "line_id": None},
                    ],
                },
            )
        assert r.status_code == 400, r.text
        assert "line_id" in r.json()["detail"].lower()
    finally:
        _delete_user(carol_id)


# ---------------------------------------------------------------------------
# 4) Business rule: scope=global with line_id 鈫?400
# ---------------------------------------------------------------------------


def test_patch_global_scope_rejects_line_id(postgres_available_v2):
    _ensure_migration_applied()
    _create_admin_user()
    dave_id = _create_user("dave")
    _add_role(dave_id, "admin", "global", None)
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{dave_id}/v2-roles",
                json={
                    "bindings": [
                        {"role": "admin", "scope": "global", "line_id": "residential"},
                    ],
                },
            )
        assert r.status_code == 400, r.text
        assert "line_id" in r.json()["detail"].lower()
    finally:
        _delete_user(dave_id)


# ---------------------------------------------------------------------------
# 5) Business rule: line-scoped role paired with scope=global 鈫?400
# ---------------------------------------------------------------------------


def test_patch_line_scoped_role_requires_business_line_scope(postgres_available_v2):
    _ensure_migration_applied()
    _create_admin_user()
    eve_id = _create_user("eve")
    _add_role(eve_id, "admin", "global", None)
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{eve_id}/v2-roles",
                json={
                    "bindings": [
                        # fin_bp is a line-scoped role 鈥?must pair with business_line
                        {"role": "fin_bp", "scope": "global", "line_id": None},
                    ],
                },
            )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"].lower()
        assert "line-scoped" in detail or "business_line" in detail
    finally:
        _delete_user(eve_id)


# ---------------------------------------------------------------------------
# 6) Business rule: global role paired with scope=business_line 鈫?400
# ---------------------------------------------------------------------------


def test_patch_global_role_requires_global_scope(postgres_available_v2):
    _ensure_migration_applied()
    _create_admin_user()
    frank_id = _create_user("frank")
    _add_role(frank_id, "admin", "global", None)
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{frank_id}/v2-roles",
                json={
                    "bindings": [
                        # admin is a global-only role 鈥?must pair with global
                        {"role": "admin", "scope": "business_line", "line_id": "residential"},
                    ],
                },
            )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"].lower()
        assert "global-only" in detail or "global" in detail
    finally:
        _delete_user(frank_id)


# ---------------------------------------------------------------------------
# 7) Business rule: duplicate (role, line_id) 鈫?400
# ---------------------------------------------------------------------------


def test_patch_duplicate_binding_rejected(postgres_available_v2):
    _ensure_migration_applied()
    _create_admin_user()
    grace_id = _create_user("grace")
    _add_role(grace_id, "admin", "global", None)
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{grace_id}/v2-roles",
                json={
                    "bindings": [
                        {"role": "fin_bp", "scope": "business_line", "line_id": "residential"},
                        {"role": "fin_bp", "scope": "business_line", "line_id": "residential"},
                    ],
                },
            )
        assert r.status_code == 400, r.text
        assert "duplicate" in r.json()["detail"].lower()
    finally:
        _delete_user(grace_id)


# ---------------------------------------------------------------------------
# 8) Business rule: empty bindings 鈫?400
# ---------------------------------------------------------------------------


def test_patch_empty_bindings_rejected(postgres_available_v2):
    _ensure_migration_applied()
    _create_admin_user()
    henry_id = _create_user("henry")
    _add_role(henry_id, "admin", "global", None)
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{henry_id}/v2-roles",
                json={"bindings": []},
            )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"].lower()
        assert "empty" in detail or "admin" in detail
    finally:
        _delete_user(henry_id)


# ---------------------------------------------------------------------------
# 9) Business rule: removing the last admin 鈫?409
# ---------------------------------------------------------------------------


def test_patch_cannot_remove_last_admin(postgres_available_v2):
    """Only one admin in the DB 鈫?PATCH that drops admin 鈫?409."""
    _ensure_migration_applied()
    for aid in _admin_user_ids():
        _delete_user(aid)
    admin_id = _create_user("lonely-admin")
    _add_role(admin_id, "admin", "global", None)
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{admin_id}/v2-roles",
                json={
                    "bindings": [
                        # drop admin, replace with viewer only 鈥?must trip
                        # last-admin protection
                        {"role": "viewer", "scope": "global", "line_id": None},
                    ],
                },
            )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"].lower()
        assert "last admin" in detail or "admin" in detail
        assert ("admin", "global", None) in _user_roles(admin_id)
    finally:
        _delete_user(admin_id)


def test_patch_can_remove_admin_when_others_remain(postgres_available_v2):
    """If multiple admins exist, demoting one of them is allowed."""
    _ensure_migration_applied()
    for aid in _admin_user_ids():
        _delete_user(aid)
    admin1 = _create_user("admin-one")
    admin2 = _create_user("admin-two")
    _add_role(admin1, "admin", "global", None)
    _add_role(admin2, "admin", "global", None)
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{admin1}/v2-roles",
                json={
                    "bindings": [
                        # demote admin-one to viewer; admin-two is still admin
                        {"role": "viewer", "scope": "global", "line_id": None},
                    ],
                },
            )
        assert r.status_code == 200, r.text
        assert _user_roles(admin1) == [("viewer", "global", None)]
        assert _user_roles(admin2) == [("admin", "global", None)]
    finally:
        _delete_user(admin1)
        _delete_user(admin2)


# ---------------------------------------------------------------------------
# 10) Business rule: non-admin caller 鈫?403
# ---------------------------------------------------------------------------


def test_patch_v2_roles_requires_admin(postgres_available_v2):
    """A non-admin calling PATCH /v2-roles gets 403 from require_admin_dep."""
    _ensure_migration_applied()
    _create_admin_user()
    iris_id = _create_user("iris")
    _add_role(iris_id, "admin", "global", None)
    try:
        with _nonadmin_client() as c:
            r = c.patch(
                f"/api/auth/users/{iris_id}/v2-roles",
                json={
                    "bindings": [
                        {"role": "viewer", "scope": "global", "line_id": None},
                    ],
                },
            )
        assert r.status_code == 403, r.text
        assert _user_roles(iris_id) == [("admin", "global", None)]
    finally:
        _delete_user(iris_id)


def test_get_v2_roles_requires_admin(postgres_available_v2):
    _ensure_migration_applied()
    _create_admin_user()
    jack_id = _create_user("jack")
    _add_role(jack_id, "admin", "global", None)
    try:
        with _nonadmin_client() as c:
            r = c.get(f"/api/auth/users/{jack_id}/v2-roles")
        assert r.status_code == 403, r.text
    finally:
        _delete_user(jack_id)


# ---------------------------------------------------------------------------
# 11) Backward compat: GET /api/auth/users includes v2_bindings
# ---------------------------------------------------------------------------


def test_user_list_includes_v2_bindings(postgres_available_v2):
    """The list endpoint (admin-only) surfaces v2_bindings on every item."""
    _ensure_migration_applied()
    _create_admin_user()
    kate_id = _create_user("kate")
    _add_role(kate_id, "admin", "global", None)
    _add_role(kate_id, "fin_bp", "business_line", "valuation")
    try:
        with _admin_client() as c:
            r = c.get("/api/auth/users")
        assert r.status_code == 200, r.text
        body = r.json()
        by_username = {u["username"]: u for u in body["users"]}
        assert "kate" in by_username
        kate = by_username["kate"]
        assert "v2_bindings" in kate
        roles = {b["role"] for b in kate["v2_bindings"]}
        assert roles == {"admin", "fin_bp"}
        # legacy v1 fields still present and unchanged
        assert "roles" in kate
        assert "admin" in kate["roles"]
        assert "accessible_lines" in kate
    finally:
        _delete_user(kate_id)


# ---------------------------------------------------------------------------
# 12) Backward compat: user_business_lines is resynced from bindings
# ---------------------------------------------------------------------------


def test_patch_resyncs_user_business_lines(postgres_available_v2):
    """After PATCH, user_business_lines reflects the union of the new line_ids."""
    _ensure_migration_applied()
    _create_admin_user()
    leo_id = _create_user("leo")
    _add_role(leo_id, "admin", "global", None)
    new_bindings = [
        {"role": "fin_bp", "scope": "business_line", "line_id": "residential"},
        {"role": "hr_bp", "scope": "business_line", "line_id": "retail"},
        {"role": "line_owner", "scope": "business_line", "line_id": "valuation"},
    ]
    try:
        with _admin_client() as c:
            r = c.patch(
                f"/api/auth/users/{leo_id}/v2-roles",
                json={"bindings": new_bindings},
            )
        assert r.status_code == 200, r.text
        lines = _user_lines(leo_id)
        assert sorted(lines) == ["residential", "retail", "valuation"]
    finally:
        _delete_user(leo_id)


# ---------------------------------------------------------------------------
# 13) Edge: PATCH /v2-roles 404s on missing user
# ---------------------------------------------------------------------------


def test_patch_v2_roles_unknown_user_returns_404(postgres_available_v2):
    """PATCH against a non-existent user_id returns 404."""
    with _admin_client() as c:
        r = c.patch(
            "/api/auth/users/999999/v2-roles",
            json={"bindings": [{"role": "admin", "scope": "global", "line_id": None}]},
        )
    assert r.status_code == 404, r.text


def test_get_v2_roles_unknown_user_returns_404(postgres_available_v2):
    with _admin_client() as c:
        r = c.get("/api/auth/users/999999/v2-roles")
    assert r.status_code == 404, r.text
