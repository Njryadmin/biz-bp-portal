"""
apps/api/tests/test_api.py

End-to-end test of the FastAPI app using its TestClient. Mounts the
business-line loader, ensures the /api/registry/lines endpoint works,
and ensures the generic /healthz endpoint is reachable.

RBAC: these tests use the ``app_with_auth`` fixture (which overrides
``get_current_user`` to an admin) so the legacy contract — that
``/api/registry/lines`` returns 200 — is preserved.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_app_starts(app_with_auth):
    with TestClient(app_with_auth) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_registry_endpoint(app_with_auth):
    with TestClient(app_with_auth) as client:
        r = client.get("/api/registry/lines")
        assert r.status_code == 200
        data = r.json()
        assert "version" in data
        assert "lines" in data
        assert isinstance(data["lines"], list)
        # The registry may be empty or populated (T1/T2 may register lines).
        # We only assert the contract holds: every line summary has the
        # cockpit-required fields.
        for line in data["lines"]:
            for k in ("id", "name", "display_name", "icon",
                      "indicators_count", "nav", "api_prefix"):
                assert k in line, f"missing key in summary: {k}"


def test_registry_endpoint_shape_keys(app_with_auth):
    """Response must always carry `version` and a `lines` array. Each line
    (even when empty) is expected to be a list of summaries with id/name/
    display_name/icon/indicators_count/nav/api_prefix — when lines exist."""
    with TestClient(app_with_auth) as client:
        r = client.get("/api/registry/lines")
        assert r.status_code == 200
        data = r.json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert "lines" in data
        assert isinstance(data["lines"], list)
        # When the registry is empty, lines must be a list (not None).
        if data["lines"]:
            first = data["lines"][0]
            for k in ("id", "name", "display_name", "icon", "indicators_count",
                      "nav", "api_prefix"):
                assert k in first, f"missing key in summary: {k}"


def test_root_endpoint(app_with_auth):
    with TestClient(app_with_auth) as client:
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data["service"] == "fin-bp-portal-api"
        assert "registry" in data


def test_registry_unauthenticated_returns_401():
    """Without auth, /api/registry/lines must 401."""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/registry/lines")
        assert r.status_code == 401
