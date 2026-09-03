"""
apps/api/tests/conftest.py

Pytest config. Ensures CWD is the monorepo root so that the registry loader
finds `business_lines/registry.yaml`.

Also provides RBAC-friendly fixtures: an `app` factory that returns a
FastAPI app with the ``get_current_user`` dependency overridden to a
mock admin, so the existing test suite (which pre-dates the RBAC
system) can keep running without changes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Repo root = two levels up from this file (apps/api/tests -> repo root)
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_TESTS_DIR = _HERE
_API_DIR = _HERE.parents[1]
_APP_DIR = _API_DIR / "app"


def _ensure_on_path() -> None:
    for p in (str(_REPO_ROOT), str(_API_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)


@pytest.fixture(scope="session", autouse=True)
def _setup_paths():
    _ensure_on_path()
    # Tell the registry loader where the project root is.
    os.environ["FIN_BP_PROJECT_ROOT"] = str(_REPO_ROOT)
    # Ensure a deterministic JWT secret for the session (32+ chars so
    # PyJWT doesn't warn about weak HMAC keys).
    os.environ.setdefault(
        "JWT_SECRET",
        "test-jwt-secret-for-rbac-tests-not-for-production-32-chars-min",
    )
    yield


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


# ---------------------------------------------------------------------------
# RBAC fixtures
# ---------------------------------------------------------------------------


def _make_current_user(roles=None, accessible_lines=None, *, uid=1, username="admin"):
    """Build a CurrentUser-like object for use with dependency overrides."""
    from app.core.auth import CurrentUser
    return CurrentUser(
        id=uid,
        username=username,
        display_name=username.title(),
        email=f"{username}@test.local",
        is_active=True,
        roles=list(roles or []),
        accessible_lines=list(accessible_lines or []),
    )


@pytest.fixture
def mock_admin_user():
    """Returns a CurrentUser instance with the admin role."""
    return _make_current_user(
        roles=["admin"],
        accessible_lines=[],
        uid=1,
        username="admin",
    )


@pytest.fixture
def mock_auditor_user():
    return _make_current_user(
        roles=["auditor"],
        accessible_lines=[],
        uid=2,
        username="auditor",
    )


@pytest.fixture
def mock_viewer_user():
    return _make_current_user(
        roles=["viewer"],
        accessible_lines=[],
        uid=3,
        username="viewer",
    )


@pytest.fixture
def mock_bp_residential_user():
    return _make_current_user(
        roles=["bp:residential"],
        accessible_lines=["residential"],
        uid=4,
        username="bp-residential",
    )


@pytest.fixture
def mock_no_role_user():
    return _make_current_user(
        roles=[],
        accessible_lines=[],
        uid=99,
        username="noone",
    )


@pytest.fixture
def app_with_auth(mock_admin_user):
    """Build the FastAPI app with ``get_current_user`` overridden to admin.

    The audit middleware + every protected endpoint become accessible
    with a single fixture so the legacy test suite still passes.
    """
    from app.main import create_app
    from app.core.auth import get_current_user

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: mock_admin_user
    return app


@pytest.fixture(autouse=True)
def _disable_audit_middleware_in_tests(monkeypatch):
    """Tests don't need the audit DB write — replace the row writer
    with a no-op so the request doesn't block on a 2-second DB
    timeout when Postgres isn't available.

    Also patch the seed_initial_users lifespan hook so it doesn't
    hammer the DB at app start.
    """
    from app.middleware import audit as audit_mod
    from app.db import seed_users as _seed_mod

    async def _noop(**kwargs):  # pragma: no cover — exercised by every test
        return None

    # Replace both the writer and the scheduler to avoid the
    # background-task pile-up in the TestClient's event loop.
    monkeypatch.setattr(audit_mod, "_write_audit_row", _noop)
    monkeypatch.setattr(audit_mod, "_schedule_audit_row", lambda **kw: None)

    # Short-circuit the seed_initial_users lifespan hook.
    async def _seed_noop():
        return {"admin": 0, "bp_users": 0}
    monkeypatch.setattr(_seed_mod, "seed_initial_users", _seed_noop)
    # And the reference in main.lifespan (imported at module load).
    import app.main as _main_mod
    monkeypatch.setattr(_main_mod, "seed_initial_users", _seed_noop)
    yield


@pytest.fixture
def client_with_auth(app_with_auth):
    with TestClient(app_with_auth) as c:
        yield c

