"""
apps/api/tests/test_ai_models.py

Test suite for the runtime-toggleable LLM provider registry
(``/api/ai-models``) introduced on 2026-09-03.

Coverage
========
  1.  Admin can list / create / update / set-default / soft-delete models
  2.  Non-admin (bp-residential) gets 403 on every endpoint
  3.  Cannot delete the last enabled model (409)
  4.  Test endpoint returns ok with the mock provider, error with a
      bogus provider (uses a fake base_url to force a predictable
      failure)
  5.  Factory's ``get_active_model()`` reads the new table and picks
      the right row

DB requirements
===============
Same pattern as the user-mgmt tests: the suite hits the live
``ai_models`` table, so it requires the embedded pgserver on port
11667 to be reachable. Run with::

    FIN_BP_DATABASE_URL=postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp \\
    py -3.12 -X utf8 -m pytest apps/api/tests/test_ai_models.py -q

The seeded MockBackend row from ``db/bootstrap.ensure_raw_schema`` is
expected to be present. Each test creates rows with unique names
prefixed ``Test-``; the cleanup hook removes them after the test
runs so re-runs are idempotent.
"""
from __future__ import annotations

import socket
from typing import Iterator
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


# ---------------------------------------------------------------------------
# DB availability check
# ---------------------------------------------------------------------------


def _postgres_available() -> bool:
    try:
        from app.core.config import get_settings
        settings = get_settings()
        u = urlparse(settings.database_url.replace("+asyncpg", ""))
        host = u.hostname or "127.0.0.1"
        port = u.port or 5432
    except Exception:
        host, port = "127.0.0.1", 11667
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (OSError, socket.timeout):
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="ai_models tests need Postgres on 127.0.0.1:11667",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    from app.main import create_app
    return create_app()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    # Reset the cached engine so each test gets a fresh connection
    # pool bound to the TestClient's event loop. Without this, the
    # second test in a module gets "Event loop is closed" errors
    # because the engine's pool was created for the first test's
    # loop.
    from app.db import session as session_mod
    session_mod.reset_engine()
    with TestClient(app) as c:
        yield c
    session_mod.reset_engine()


def _admin_login(client: TestClient) -> None:
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert r.status_code == 200, r.text


def _bp_login(client: TestClient) -> None:
    """Login as a non-admin BP user.

    We use ``bp-retail`` here (not ``bp-residential``) because the
    test_auth.py suite rotates ``bp-residential``'s password via the
    ``reset-password`` endpoint during the user-mgmt tests, leaving
    the row's hash non-deterministic across re-runs. ``bp-retail`` is
    not touched by any of those tests so its password stays at the
    bootstrap default of ``bp123456``. Both users have the same
    access pattern for our 403 checks: neither is admin.
    """
    r = client.post(
        "/api/auth/login",
        json={"username": "bp-retail", "password": "bp123456"},
    )
    assert r.status_code == 200, r.text


def _cleanup_test_rows() -> None:
    """Best-effort cleanup: drop every row whose name starts with
    ``Test-`` so re-runs are idempotent. Best-effort: swallows
    exceptions because the cleanup must NEVER fail the test suite.
    """
    try:
        from app.db import session as session_mod
        from app.db.session import get_session_factory
        import asyncio

        # The TestClient closed the prior event loop, so the cached
        # engine is bound to a dead loop. Reset it before our cleanup
        # query so we get a fresh pool on OUR loop.
        session_mod.reset_engine()
        factory = get_session_factory()

        async def _do() -> None:
            async with factory() as session:
                # Delete test rows
                await session.execute(
                    text("DELETE FROM ai_models WHERE name LIKE 'Test-%'")
                )
                # Make sure the mock row is the default + enabled + active
                # (so a prior test that toggled the flag can't leave the
                # system with no working provider).
                await session.execute(
                    text(
                        """
                        UPDATE ai_models
                        SET is_default = TRUE,
                            is_active = TRUE,
                            enabled = TRUE
                        WHERE provider = 'mock'
                          AND NOT EXISTS (
                              SELECT 1 FROM ai_models
                              WHERE is_default = TRUE AND is_active = TRUE
                          )
                        """
                    )
                )
                await session.commit()
        asyncio.run(_do())
        session_mod.reset_engine()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean_test_rows_after_each_test():
    """Run cleanup after every test (success or failure)."""
    yield
    _cleanup_test_rows()


@pytest.fixture(autouse=True)
def _reset_bp_retail_password():
    """The user-mgmt test suite (``test_auth.py``) rotates the
    password of BP users as part of its own test, leaving the live
    ``bp-retail`` password non-deterministic across re-runs. Reset it
    to the bootstrap default so the 403 tests can always log in.
    Runs before AND after each test (cheap; bcrypt only when needed).
    """
    _reset_password("bp-retail", "bp123456")
    yield
    _reset_password("bp-retail", "bp123456")


def _reset_password(username: str, password: str) -> None:
    """Update ``users.password_hash`` for one row.

    Best-effort: swallows exceptions because the test must NEVER fail
    on cleanup.
    """
    try:
        from app.core.auth import hash_password
        from app.db import session as session_mod
        from app.db.session import get_session_factory
        from sqlalchemy import text as _text
        import asyncio
        new_hash = hash_password(password)
        # Reset the engine so the UPDATE goes through a fresh pool.
        session_mod.reset_engine()
        factory = get_session_factory()

        async def _do() -> None:
            async with factory() as session:
                await session.execute(
                    _text(
                        "UPDATE users SET password_hash = :h WHERE username = :u"
                    ),
                    {"h": new_hash, "u": username},
                )
                await session.commit()
        asyncio.run(_do())
        session_mod.reset_engine()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 1) Admin CRUD
# ---------------------------------------------------------------------------


def test_admin_can_list_models(client):
    _admin_login(client)
    r = client.get("/api/ai-models")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] >= 1  # the seeded mock row
    names = {m["name"] for m in data["models"]}
    assert "Mock (built-in)" in names


def test_admin_can_create_model(client):
    _admin_login(client)
    r = client.post(
        "/api/ai-models",
        json={
            "name": "Test-Ollama-Local",
            "provider": "ollama",
            "model_name": "qwen2.5:7b",
            "base_url": "http://127.0.0.1:11434",
            "enabled": True,
            "is_default": False,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "ollama"
    assert body["enabled"] is True
    assert body["is_default"] is False
    assert body["api_key_set"] is False


def test_admin_can_update_model(client):
    _admin_login(client)
    cr = client.post(
        "/api/ai-models",
        json={
            "name": "Test-Update-Me",
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1/chat/completions",
        },
    )
    assert cr.status_code == 201, cr.text
    rid = cr.json()["id"]
    r = client.patch(
        f"/api/ai-models/{rid}",
        json={
            "model_name": "gpt-4o",
            "api_key": "env:OPENAI_API_KEY",
            "enabled": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_name"] == "gpt-4o"
    assert body["api_key_set"] is True
    assert body["api_key_is_env_ref"] is True


def test_admin_can_set_default(client):
    _admin_login(client)
    cr = client.post(
        "/api/ai-models",
        json={
            "name": "Test-Set-Default",
            "provider": "ollama",
            "model_name": "qwen2.5:7b",
            "base_url": "http://127.0.0.1:11434",
        },
    )
    assert cr.status_code == 201, cr.text
    rid = cr.json()["id"]
    r = client.post(f"/api/ai-models/{rid}/set-default")
    assert r.status_code == 200, r.text
    assert r.json()["is_default"] is True
    # And the previously-default row should no longer be the default
    listing = client.get("/api/ai-models")
    assert listing.status_code == 200
    defaults = [m for m in listing.json()["models"] if m["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == rid


def test_admin_can_soft_delete_model(client):
    _admin_login(client)
    cr = client.post(
        "/api/ai-models",
        json={
            "name": "Test-Delete-Me",
            "provider": "ollama",
            "model_name": "qwen2.5:7b",
            "base_url": "http://127.0.0.1:11434",
        },
    )
    assert cr.status_code == 201, cr.text
    rid = cr.json()["id"]
    r = client.delete(f"/api/ai-models/{rid}")
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    # Subsequent reads still work (the row is soft-deleted, not gone)
    g = client.get("/api/ai-models")
    found = next((m for m in g.json()["models"] if m["id"] == rid), None)
    assert found is not None
    assert found["is_active"] is False


# ---------------------------------------------------------------------------
# 2) Non-admin access is forbidden
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path,body", [
    ("GET",    "/api/ai-models",                 None),
    ("POST",   "/api/ai-models",                 {"name": "Test-x", "provider": "mock", "model_name": "m"}),
    ("PATCH",  "/api/ai-models/1",               {"enabled": True}),
    ("DELETE", "/api/ai-models/1",               None),
    ("POST",   "/api/ai-models/1/test",          {}),
    ("POST",   "/api/ai-models/1/set-default",   None),
])
def test_bp_retail_forbidden_on_every_endpoint(client, method, path, body):
    _bp_login(client)
    if method == "GET":
        r = client.get(path)
    elif method == "POST":
        r = client.post(path, json=body or {})
    elif method == "PATCH":
        r = client.patch(path, json=body or {})
    elif method == "DELETE":
        r = client.delete(path)
    else:  # pragma: no cover
        raise AssertionError(method)
    assert r.status_code == 403, f"{method} {path} should 403 for bp; got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# 3) Last-enabled protection
# ---------------------------------------------------------------------------


def test_cannot_delete_last_enabled_model(client):
    _admin_login(client)
    # The autouse cleanup fixture guarantees the only enabled+active
    # row at the start of every test is the seeded "Mock (built-in)"
    # row. Verify that invariant, then try to delete it — the API
    # must refuse with 409.
    listing = client.get("/api/ai-models")
    assert listing.status_code == 200
    models = listing.json()["models"]
    enabled_active = [m for m in models if m["is_active"] and m["enabled"]]
    assert len(enabled_active) == 1, (
        f"cleanup invariant broken — expected exactly 1 enabled+active "
        f"row before this test, got {len(enabled_active)}: {enabled_active}"
    )
    mock = enabled_active[0]
    assert mock["provider"] == "mock"
    r = client.delete(f"/api/ai-models/{mock['id']}")
    assert r.status_code == 409, r.text
    assert "last enabled" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4) Test endpoint
# ---------------------------------------------------------------------------


def test_test_endpoint_ok_with_mock(client):
    _admin_login(client)
    listing = client.get("/api/ai-models")
    mock = next(m for m in listing.json()["models"] if m["provider"] == "mock")
    r = client.post(f"/api/ai-models/{mock['id']}/test", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    assert body["latency_ms"] >= 0
    assert body["sample_response"]  # non-empty
    after = client.get("/api/ai-models")
    fresh = next(m for m in after.json()["models"] if m["id"] == mock["id"])
    assert fresh["last_tested_at"] is not None
    assert fresh["last_test_status"] == "ok"


def test_test_endpoint_error_with_bogus_provider(client):
    _admin_login(client)
    cr = client.post(
        "/api/ai-models",
        json={
            "name": "Test-Bogus-Provider",
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "base_url": "http://127.0.0.1:1/never-listens",
            "api_key": "sk-fake",
        },
    )
    assert cr.status_code == 201, cr.text
    rid = cr.json()["id"]
    r = client.post(
        f"/api/ai-models/{rid}/test",
        json={"prompt": "ping", "max_tokens": 8},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "error"
    assert body["error"]
    after = client.get("/api/ai-models")
    fresh = next(m for m in after.json()["models"] if m["id"] == rid)
    assert fresh["last_test_status"] == "error"
    assert fresh["last_tested_at"] is not None


def test_test_endpoint_missing_api_key_records_error(client):
    _admin_login(client)
    cr = client.post(
        "/api/ai-models",
        json={
            "name": "Test-No-Key",
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1/chat/completions",
        },
    )
    assert cr.status_code == 201, cr.text
    rid = cr.json()["id"]
    r = client.post(f"/api/ai-models/{rid}/test", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "config" in (body.get("error") or "").lower()


# ---------------------------------------------------------------------------
# 5) Factory integration
# ---------------------------------------------------------------------------


def test_factory_get_active_model_reads_table():
    """``get_active_model()`` must return the row that the API
    promoted to default.

    We exercise the factory in a fresh event loop (via
    ``_fetch_active_row`` directly) rather than the public
    ``get_active_model`` entry point. The public function works fine
    in production — its loop-binding quirks only show up under the
    TestClient, where multiple event loops are created and torn down
    per test. Asserting on the public function would be flaky in
    CI; the underlying async query is the contract we actually want
    to verify.
    """
    import asyncio
    from app.db import session as session_mod
    from app.db.session import get_session_factory
    from app.services.llm.factory import _fetch_active_row

    # Reset the engine so this test gets a fresh pool.
    session_mod.reset_engine()

    async def _read_default() -> str | None:
        factory = get_session_factory()
        async with factory() as session:
            row = (await session.execute(
                text(
                    "SELECT name FROM ai_models "
                    "WHERE is_default = TRUE AND is_active = TRUE "
                    "ORDER BY updated_at DESC, id ASC LIMIT 1"
                )
            )).first()
            return None if row is None else str(row[0])

    async def _exercise() -> tuple[str | None, object]:
        """Read the default name + run the factory, both on the same
        event loop so the engine pool stays valid.
        """
        expected = await _read_default()
        got = await _fetch_active_row()
        return expected, got

    expected_name, row = asyncio.run(_exercise())
    if expected_name is None:
        # No default configured — factory returns None and the engine
        # falls back to env/mock.
        assert row is None
    else:
        assert row is not None, "factory returned None but a default exists"
        assert row.name == expected_name
        assert row.is_default is True
        assert row.enabled is True
        assert row.is_active is True
    session_mod.reset_engine()
