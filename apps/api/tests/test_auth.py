"""
apps/api/tests/test_auth.py

Comprehensive test suite for the RBAC system introduced on 2026-09-03.

Coverage
========
  1.  Password hashing (bcrypt round-trip + wrong-password rejection)
  2.  JWT encode/decode round-trip + tampering detection
  3.  Cookie set on login / cleared on logout
  4.  /api/auth/me with + without cookie
  5.  /api/auth/login — bad creds → 401; locked user → 401
  6.  Registry list_lines requires auth
  7.  Business-line enforcement: bp:<line> cannot see other lines
  8.  Cross-engine coverage: sensitivity/forecast/alerts/copilot all
      require auth
  9.  Copilot system prompt includes the active user
 10.  Scrapers run is admin-only
 11.  Upload is admin/auditor-only
 12.  User management: list / create / patch / delete
 13.  Audit log rows are written for authenticated requests
 14.  Bootstrap (admin + 10 BP users) idempotent
 15.  Last-admin protection: cannot demote / delete the only admin

Note on DB
==========
A subset of admin-endpoint tests (user list / create / patch / delete /
audit-log query) hit the real ``raw.audit_log`` and ``users`` tables.
Those tests are gated by the ``postgres_available`` fixture and are
skipped when the live database is unreachable. They will pass in the
Docker stack where Postgres is always up.
"""
from __future__ import annotations

import os
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

# Set a deterministic JWT secret for this test session BEFORE the app
# modules are imported.
os.environ.setdefault(
    "JWT_SECRET", "test-jwt-secret-for-rbac-tests-not-for-production"
)


# ---------------------------------------------------------------------------
# Local helpers — we can't rely on a real Postgres in CI, so we patch the
# ``_load_user_by_id`` function in ``app.core.auth`` to read from a fake
# in-memory store that the tests control.
# ---------------------------------------------------------------------------


class _FakeUserStore:
    """Minimal in-memory replacement for the users/user_roles/user_business_lines
    Postgres tables. Used by every test in this file via monkeypatch.
    """

    def __init__(self) -> None:
        self.users: dict[int, dict] = {}
        self.roles: dict[int, set[str]] = {}
        self.lines: dict[int, set[str]] = {}
        self._next_id: int = 1

    def add(
        self,
        username: str,
        password: str,
        *,
        roles: list[str] | None = None,
        accessible_lines: list[str] | None = None,
        is_active: bool = True,
        email: str | None = None,
    ) -> int:
        from app.core.auth import hash_password
        uid = self._next_id
        self._next_id += 1
        self.users[uid] = {
            "id": uid,
            "username": username,
            "email": email or f"{username}@test.local",
            "password_hash": hash_password(password),
            "display_name": username.title(),
            "is_active": is_active,
        }
        self.roles[uid] = set(roles or [])
        self.lines[uid] = set(accessible_lines or [])
        return uid

    def set_roles(self, uid: int, roles: list[str]) -> None:
        self.roles[uid] = set(roles)

    def set_lines(self, uid: int, lines: list[str]) -> None:
        self.lines[uid] = set(lines)

    def deactivate(self, uid: int) -> None:
        if uid in self.users:
            self.users[uid]["is_active"] = False


@pytest.fixture
def fake_store(monkeypatch) -> _FakeUserStore:
    """Replace the auth module's user loader with a fake store."""
    store = _FakeUserStore()
    from app.core import auth as auth_mod
    from app.core.auth import CurrentUser, verify_password as _verify_pw

    async def _fake_load(uid: int) -> CurrentUser | None:
        u = store.users.get(uid)
        if not u or not u["is_active"]:
            return None
        return CurrentUser(
            id=u["id"],
            username=u["username"],
            display_name=u["display_name"],
            email=u["email"],
            is_active=u["is_active"],
            roles=sorted(store.roles.get(uid, set())),
            accessible_lines=sorted(store.lines.get(uid, set())),
        )

    async def _fake_load_credentials(username: str, password: str) -> CurrentUser | None:
        for u in store.users.values():
            if u["username"] == username and u["is_active"]:
                if _verify_pw(password, u["password_hash"]):
                    return await _fake_load(u["id"])
        return None

    # Patch the user lookup used by get_current_user
    monkeypatch.setattr(auth_mod, "_load_user_by_id", _fake_load)
    # Patch the credential check used by /api/auth/login
    monkeypatch.setattr(
        "app.routers.auth._load_user_by_credentials", _fake_load_credentials
    )
    return store


@pytest.fixture
def store_with_users(fake_store) -> _FakeUserStore:
    """Seed a fresh store with the canonical user set used across the
    test suite."""
    fake_store.add("admin", "admin123", roles=["admin", "auditor"])
    fake_store.add("viewer", "viewer123", roles=["viewer"])
    fake_store.add("auditor", "audit123", roles=["auditor"])
    fake_store.add("bp-residential", "bp123456",
                   roles=["bp:residential"], accessible_lines=["residential"])
    fake_store.add("bp-retail", "bp123456",
                   roles=["bp:retail"], accessible_lines=["retail"])
    return fake_store


@pytest.fixture
def postgres_available():
    """Skip the test if Postgres is unreachable.

    The admin-endpoint tests (user CRUD + audit log query) hit the
    real DB tables; without Postgres they time out (2s/req) and the
    suite becomes too slow. In CI / Docker, Postgres is always up.
    """
    import socket
    from app.core.config import get_settings
    settings = get_settings()
    # The DSN is ``postgresql+asyncpg://user:pw@host:port/db`` — extract host/port.
    try:
        # crude parse
        from urllib.parse import urlparse
        u = urlparse(settings.database_url.replace("+asyncpg", ""))
        host = u.hostname or "localhost"
        port = u.port or 5432
    except Exception:
        host, port = "localhost", 5432
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (OSError, socket.timeout):
        pytest.skip(f"Postgres not reachable at {host}:{port} — admin CRUD tests skipped")


# ---------------------------------------------------------------------------
# Test app fixture — uses the same TestClient + dependency-overrides
# pattern as test_api.py
# ---------------------------------------------------------------------------


def _make_app():
    from app.main import create_app
    return create_app()


@pytest.fixture
def client(fake_store) -> Iterator[TestClient]:
    app = _make_app()
    # The business-line middleware reads users from the DB; with a fake
    # store, we must disable the line-guard on the sub-app so the
    # integration tests can exercise the per-line check at the
    # FastAPI dependency level instead. The simplest path: leave the
    # guards in place (they call _load_user_by_id which we patched
    # above) and let them resolve the fake user.
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 1) Password hashing
# ---------------------------------------------------------------------------


def test_hash_password_and_verify_roundtrip():
    from app.core.auth import hash_password, verify_password
    h = hash_password("hello-world")
    assert h != "hello-world"
    assert verify_password("hello-world", h) is True
    assert verify_password("wrong", h) is False


def test_hash_produces_different_salts():
    from app.core.auth import hash_password
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b  # bcrypt includes a per-call salt


# ---------------------------------------------------------------------------
# 2) JWT round-trip
# ---------------------------------------------------------------------------


def test_jwt_encode_decode_roundtrip():
    from app.core.auth import create_access_token, decode_token
    tok = create_access_token(
        user_id=42,
        username="alice",
        roles=["admin"],
        accessible_lines=["residential"],
    )
    payload = decode_token(tok)
    assert payload.sub == 42
    assert payload.username == "alice"
    assert payload.roles == ["admin"]
    assert payload.accessible_lines == ["residential"]


def test_jwt_tampered_token_rejected():
    from app.core.auth import create_access_token, decode_token
    import jwt as pyjwt
    tok = create_access_token(1, "alice", ["viewer"], [])
    # Flip a byte in the signature section
    parts = tok.split(".")
    parts[2] = "A" * len(parts[2])
    bad = ".".join(parts)
    with pytest.raises(Exception):
        decode_token(bad)


def test_jwt_wrong_secret_rejected(monkeypatch):
    from app.core import auth as auth_mod
    monkeypatch.setenv("JWT_SECRET", "first-secret")
    from app.core.auth import create_access_token
    tok = create_access_token(1, "alice", ["viewer"], [])
    monkeypatch.setenv("JWT_SECRET", "different-secret")
    # Re-import to pick up the new secret (decode_token reads the env)
    with pytest.raises(Exception):
        auth_mod.decode_token(tok)


# ---------------------------------------------------------------------------
# 3) /api/auth/login (cookie set) + /me
# ---------------------------------------------------------------------------


def test_login_sets_cookie_and_returns_me(client, store_with_users):
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "admin"
    assert "admin" in body["roles"]
    # cookie was set
    assert "finbp_token" in r.cookies


def test_login_wrong_password_returns_401(client, store_with_users):
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert r.status_code == 401
    assert "finbp_token" not in r.cookies


def test_login_unknown_user_returns_401(client, store_with_users):
    r = client.post(
        "/api/auth/login", json={"username": "ghost", "password": "x"}
    )
    assert r.status_code == 401


def test_login_inactive_user_returns_401(client, store_with_users):
    store_with_users.deactivate(
        next(uid for uid, u in store_with_users.users.items()
             if u["username"] == "admin")
    )
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert r.status_code == 401


def test_me_without_cookie_returns_401(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_with_cookie_returns_user(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "bp-residential", "password": "bp123456"}
    )
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "bp-residential"
    assert body["roles"] == ["bp:residential"]
    assert body["accessible_lines"] == ["residential"]


def test_logout_clears_cookie(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "viewer", "password": "viewer123"}
    )
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    # cookie was cleared
    assert "finbp_token" not in r.cookies or r.cookies.get("finbp_token") == ""


# ---------------------------------------------------------------------------
# 4) Registry list_lines requires auth + filters by accessible_lines
# ---------------------------------------------------------------------------


def test_registry_requires_auth(client):
    r = client.get("/api/registry/lines")
    assert r.status_code == 401


def test_registry_admin_sees_all_lines(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    r = client.get("/api/registry/lines")
    assert r.status_code == 200
    data = r.json()
    line_ids = {ln["id"] for ln in data["lines"]}
    # 10 registered lines expected
    assert len(line_ids) == 10
    for lid in [
        "residential", "retail", "valuation", "advisory",
        "office-leasing", "investment", "project-management", "industrial",
        "retail-leasing", "my-line",
    ]:
        assert lid in line_ids


def test_registry_bp_sees_only_their_line(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "bp-residential", "password": "bp123456"}
    )
    r = client.get("/api/registry/lines")
    assert r.status_code == 200
    data = r.json()
    line_ids = {ln["id"] for ln in data["lines"]}
    assert line_ids == {"residential"}


def test_registry_viewer_sees_all_lines(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "viewer", "password": "viewer123"}
    )
    r = client.get("/api/registry/lines")
    assert r.status_code == 200
    data = r.json()
    line_ids = {ln["id"] for ln in data["lines"]}
    assert len(line_ids) == 10


# ---------------------------------------------------------------------------
# 5) Business-line endpoint enforcement (mounting-level guard)
# ---------------------------------------------------------------------------


def test_bp_cannot_access_other_line_endpoint(client, store_with_users):
    """bp-residential hits /api/lines/retail/... — must be blocked.

    The exact route shape depends on the residential/retail routers.
    We hit a known endpoint, ``/indicators``, which every line exposes.
    """
    client.post(
        "/api/auth/login", json={"username": "bp-residential", "password": "bp123456"}
    )
    r = client.get("/api/lines/retail/indicators")
    # The mounted router uses business_line_router_guard which raises 403.
    assert r.status_code in (401, 403)


def test_bp_can_access_own_line_endpoint(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "bp-residential", "password": "bp123456"}
    )
    r = client.get("/api/lines/residential/indicators")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 6) Cross-engine coverage: sensitivity / forecast / alerts / copilot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,method", [
    ("/api/sensitivity/profiles", "GET"),
    ("/api/forecast/profiles", "GET"),
    ("/api/alerts/profiles", "GET"),
    ("/api/copilot/suggestions", "GET"),
    ("/api/copilot/health", "GET"),
    ("/api/scrapers", "GET"),
    ("/api/upload/history", "GET"),
])
def test_universal_endpoints_require_auth(client, path, method):
    if method == "GET":
        r = client.get(path)
    else:
        r = client.post(path)
    assert r.status_code == 401, f"{method} {path} should require auth; got {r.status_code}"


def test_alerts_list_profiles_filtered_for_bp(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "bp-retail", "password": "bp123456"}
    )
    r = client.get("/api/alerts/profiles")
    assert r.status_code == 200
    data = r.json()
    line_ids = {ln["line_id"] for ln in data["lines"]}
    # Only retail has an alerts.yaml
    assert line_ids == {"retail"}


def test_sensitivity_analyze_requires_business_line_access(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "bp-residential", "password": "bp123456"}
    )
    r = client.post(
        "/api/sensitivity/analyze",
        json={"line_id": "retail", "output_id": "irr", "input1_id": "rent"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 7) Copilot system prompt includes the active user
# ---------------------------------------------------------------------------


def test_copilot_system_prompt_with_active_user():
    from app.services.copilot_engine import CopilotEngine
    eng = CopilotEngine()
    eng.set_active_user(
        {
            "id": 1,
            "username": "alice",
            "display_name": "Alice",
            "roles": ["admin"],
            "accessible_lines": [],
        }
    )
    sp = eng.system_prompt_with_user()
    assert "alice" in sp
    assert "admin" in sp
    assert "RBAC" in sp or "权限" in sp


# ---------------------------------------------------------------------------
# 8) Scrapers run is admin-only
# ---------------------------------------------------------------------------


def test_scrapers_run_all_requires_admin(client, store_with_users):
    # Login as viewer (not admin)
    client.post(
        "/api/auth/login", json={"username": "viewer", "password": "viewer123"}
    )
    r = client.post("/api/scrapers/run-all")
    assert r.status_code == 403


def test_scrapers_list_succeeds_for_non_admin(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "viewer", "password": "viewer123"}
    )
    r = client.get("/api/scrapers")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 9) Upload is admin/auditor-only
# ---------------------------------------------------------------------------


def test_upload_history_requires_admin_or_auditor(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "bp-residential", "password": "bp123456"}
    )
    r = client.get("/api/upload/history")
    assert r.status_code == 403


def test_auditor_can_read_upload_history(client, store_with_users, postgres_available):
    client.post(
        "/api/auth/login", json={"username": "auditor", "password": "audit123"}
    )
    r = client.get("/api/upload/history")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 10) User management (admin only)
# ---------------------------------------------------------------------------


def test_user_list_requires_admin(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "viewer", "password": "viewer123"}
    )
    r = client.get("/api/auth/users")
    assert r.status_code == 403


def test_admin_can_list_users(client, store_with_users, postgres_available):
    client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    r = client.get("/api/auth/users")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 5
    usernames = {u["username"] for u in data["users"]}
    assert "admin" in usernames
    assert "bp-residential" in usernames


def test_admin_can_create_user(client, store_with_users, postgres_available):
    client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    r = client.post(
        "/api/auth/users",
        json={
            "username": "newbp",
            "password": "newpass1",
            "display_name": "New BP",
            "roles": ["bp:industrial"],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "newbp"
    assert "bp:industrial" in body["roles"]


def test_admin_cannot_demote_last_admin(client, store_with_users, postgres_available):
    client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    admin_uid = next(
        uid for uid, u in store_with_users.users.items() if u["username"] == "admin"
    )
    r = client.patch(
        f"/api/auth/users/{admin_uid}/roles", json={"roles": ["viewer"]}
    )
    assert r.status_code == 409
    assert "last admin" in r.json()["detail"].lower()


def test_admin_cannot_delete_self(client, store_with_users, postgres_available):
    client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    admin_uid = next(
        uid for uid, u in store_with_users.users.items() if u["username"] == "admin"
    )
    r = client.delete(f"/api/auth/users/{admin_uid}")
    assert r.status_code == 400
    assert "yourself" in r.json()["detail"].lower()


def test_admin_can_change_user_roles(client, store_with_users, postgres_available):
    client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    target_uid = next(
        uid for uid, u in store_with_users.users.items()
        if u["username"] == "bp-residential"
    )
    r = client.patch(
        f"/api/auth/users/{target_uid}/roles",
        json={"roles": ["bp:residential", "bp:retail"], "accessible_lines": ["residential", "retail"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert "bp:retail" in body["roles"]


# ---------------------------------------------------------------------------
# 11) Accessible lines endpoint
# ---------------------------------------------------------------------------


def test_accessible_lines_for_bp(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "bp-residential", "password": "bp123456"}
    )
    r = client.get("/api/auth/accessible-lines")
    assert r.status_code == 200
    data = r.json()
    assert data["lines"] == ["residential"]
    # all 10 are reported so the UI can grey out the rest
    assert len(data["all_lines"]) == 10


# ---------------------------------------------------------------------------
# 12) Audit log writes (admin/auditor)
# ---------------------------------------------------------------------------


def test_audit_log_requires_auditor_or_admin(client, store_with_users):
    client.post(
        "/api/auth/login", json={"username": "viewer", "password": "viewer123"}
    )
    r = client.get("/api/auth/audit-log")
    assert r.status_code == 403


def test_audit_log_admin_can_read(client, store_with_users, postgres_available):
    client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    # Generate some traffic first
    client.get("/api/registry/lines")
    client.get("/api/auth/me")
    r = client.get("/api/auth/audit-log?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 2
    # at least one row should be for the admin user
    assert any(item["username"] == "admin" for item in data["items"])


# ---------------------------------------------------------------------------
# 13) Bootstrap — admin + 10 BP users seeded when users table empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_creates_admin_and_bp_users(monkeypatch, tmp_path):
    """Simulate the very first boot: empty users table, 10 registry lines.

    We patch _users_empty to True (so seed runs), patch load_registry
    to return 10 fake lines, and verify the insert path is hit.
    """
    from app.db import seed_users

    inserted: list[dict] = []

    async def _fake_users_empty() -> bool:
        return True

    async def _fake_insert_user(
        *, username, password, display_name, email, role, line_id
    ):
        # Return ids 1, 2, 3, ... so we can count
        inserted.append(
            {
                "username": username,
                "role": role,
                "line_id": line_id,
                "display_name": display_name,
            }
        )
        return len(inserted)

    def _fake_registry():
        line_ids = [
            "residential", "retail", "retail-leasing", "valuation", "advisory",
            "office-leasing", "investment", "project-management", "industrial",
            "my-line",
        ]
        from app.core.registry import RegistryEntry, BusinessLine
        return [
            RegistryEntry(
                line=BusinessLine(
                    id=lid, name=lid, api_prefix=f"/api/lines/{lid}",
                    warehouse={"schema": lid, "dbt_schema": lid, "mart_schema": lid},
                ),
                manifest_path=tmp_path / f"{lid}/manifest.yaml",
            )
            for lid in line_ids
        ]

    monkeypatch.setattr(seed_users, "_users_empty", _fake_users_empty)
    monkeypatch.setattr(seed_users, "_insert_user", _fake_insert_user)
    monkeypatch.setattr(seed_users, "load_registry", _fake_registry)
    summary = await seed_users.seed_initial_users()
    assert summary == {"admin": 1, "bp_users": 10}
    assert len(inserted) == 11
    # admin is the first row
    assert inserted[0]["username"].startswith("admin")
    assert inserted[0]["role"] == "admin"
    # 10 BP users, one per line
    bp_rows = [r for r in inserted if r["role"].startswith("bp:")]
    assert len(bp_rows) == 10
    assert {r["role"] for r in bp_rows} == {
        f"bp:{lid}" for lid in [
            "residential", "retail", "retail-leasing", "valuation", "advisory",
            "office-leasing", "investment", "project-management", "industrial",
            "my-line",
        ]
    }


@pytest.mark.asyncio
async def test_bootstrap_skipped_when_users_present(monkeypatch):
    """If the users table is non-empty, seed_initial_users is a no-op."""
    from app.db import seed_users

    async def _fake_users_empty() -> bool:
        return False

    async def _fail(*args, **kwargs):  # pragma: no cover
        raise AssertionError("should not be called when users exist")

    monkeypatch.setattr(seed_users, "_users_empty", _fake_users_empty)
    monkeypatch.setattr(seed_users, "_insert_user", _fail)
    summary = await seed_users.seed_initial_users()
    assert summary == {"admin": 0, "bp_users": 0}
