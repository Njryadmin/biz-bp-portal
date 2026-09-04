"""
apps/api/tests/test_admin_business_lines.py
============================================

D1 — admin API for business-line manifest + indicators editing.

What we cover
-------------
* GET /api/admin/business-lines returns 9 lines (v0.1.0 registry)
* GET /api/admin/business-lines/{id} returns v1 + v2 fields
* PATCH description, data_scope.domains, kpis.fin_view all round-trip
* PATCH validation: bad domain, bad access_matrix domain, bad kpi id → 400
* Non-admin caller → 403
* Unknown line_id → 404
* Atomic write: a bad write does not corrupt the existing file
* Backup: .bak is created before each write

Why we patch ``registry.load_registry``'s lru_cache directly
------------------------------------------------------------
The router calls ``load_registry.cache_clear()`` after a successful
write so the next request sees fresh data. Tests need to be
deterministic — if a previous test mutated a manifest but its cache
clear didn't fire (e.g. because the test patched the body so the
write was rejected), subsequent tests could see stale data. To
avoid that, every test calls ``load_registry.cache_clear()`` at
the start.

YAML library
------------
We use PyYAML (ruamel.yaml is not in pyproject.toml at the time of
writing — see commit message). Comment preservation is a known
limitation; the test suite asserts the field semantics, not the
on-disk formatting.
"""
from __future__ import annotations

import copy
import socket
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

import pytest
import yaml
from fastapi.testclient import TestClient


def _invalidate_registry_cache() -> None:
    """Drop the registry's filesystem cache. Mirrors the helper in
    routers/admin_business_lines.py so test isolation is independent
    of the router's own behavior.

    NB: as of 2026-09-04 only ``get_project_root`` is lru_cached; the
    actual file reads go straight to disk on every ``load_registry()``
    call. We still clear both for symmetry with the router.
    """
    from app.core.registry import get_project_root, load_registry
    cache_clear = getattr(load_registry, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()
    get_project_root.cache_clear()


# ---------------------------------------------------------------------------
# Fixtures: pgserver gate + admin client
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
def postgres_available_d1():
    """pgserver gate. Skips the entire file when Postgres is unreachable."""
    cfg = _parse_pg_dsn()
    try:
        with socket.create_connection((cfg["host"], cfg["port"]), timeout=0.5):
            return cfg
    except (OSError, socket.timeout):
        pytest.skip(
            f"Postgres not reachable at {cfg['host']}:{cfg['port']} — "
            f"D1 tests skipped"
        )


@contextmanager
def _admin_client() -> Iterator[TestClient]:
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
    session_mod.reset_engine()
    # Make sure the lru_cache doesn't carry over from a previous test.
    _invalidate_registry_cache()
    with TestClient(app) as c:
        yield c
    session_mod.reset_engine()
    _invalidate_registry_cache()


@contextmanager
def _nonadmin_client() -> Iterator[TestClient]:
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
# Per-test manifest save/restore
# ---------------------------------------------------------------------------


@pytest.fixture
def manifest_files_restored(repo_root):
    """Snapshot every business-line manifest + indicators + .bak at
    the start of each test and restore at the end. This makes the
    PATCH tests safe to run repeatedly without leaving residual
    changes on disk.

    We do NOT snapshot _template/ — the spec says don't touch it.
    """
    import os
    from pathlib import Path

    bl_dir = repo_root / "business_lines"
    snapshot: dict[Path, bytes | None] = {}

    def _walk():
        for p in bl_dir.glob("*/manifest.yaml"):
            snapshot[p] = p.read_bytes()
        for p in bl_dir.glob("*/manifest.yaml.bak"):
            snapshot[p] = p.read_bytes()
        for p in bl_dir.glob("*/indicators.yaml"):
            snapshot[p] = p.read_bytes()
        for p in bl_dir.glob("*/indicators.yaml.bak"):
            snapshot[p] = p.read_bytes()

    _walk()
    try:
        yield
    finally:
        # Restore: write back the snapshot, then sweep any .bak files
        # that didn't exist before the test (they're created by the
        # atomic-write backup step).
        before_paths = set(snapshot.keys())
        for p, content in snapshot.items():
            if content is None:
                if p.exists():
                    p.unlink()
            else:
                p.write_bytes(content)
        # Remove any .bak files that did not exist before the test.
        for p in bl_dir.glob("*.yaml.bak"):
            if p not in before_paths:
                try:
                    p.unlink()
                except OSError:
                    pass
        for p in bl_dir.glob("*/*.yaml.bak"):
            if p not in before_paths:
                try:
                    p.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# 1) GET list — returns 9 lines
# ---------------------------------------------------------------------------


def test_list_business_lines_returns_all_9(
    postgres_available_d1, manifest_files_restored
):
    with _admin_client() as c:
        r = c.get("/api/admin/business-lines")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == len(body["lines"])
    assert body["count"] == 9, body["lines"]
    line_ids = {ln["id"] for ln in body["lines"]}
    expected = {
        "residential", "retail", "retail-leasing", "valuation", "advisory",
        "office-leasing", "investment", "project-management", "industrial",
    }
    assert line_ids == expected


# ---------------------------------------------------------------------------
# 2) GET list — v2 metadata flag set on project-management, false on others
# ---------------------------------------------------------------------------


def test_list_business_lines_v2_metadata(postgres_available_d1, manifest_files_restored):
    with _admin_client() as c:
        r = c.get("/api/admin/business-lines")
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {ln["id"]: ln for ln in body["lines"]}
    assert by_id["project-management"]["has_v2_fields"] is True
    # project-management is the only v2 manifest as of 2026-09-04
    for other in ("residential", "retail", "valuation"):
        assert by_id[other]["has_v2_fields"] is False
    # project-management also exposes data_scope.domains in the summary
    assert "data_scope" in by_id["project-management"]
    assert set(by_id["project-management"]["data_scope"]["domains"]) == {
        "business", "finance", "hr", "client", "project",
    }


# ---------------------------------------------------------------------------
# 3) GET single line — v1 + v2 fields present
# ---------------------------------------------------------------------------


def test_get_single_business_line_full(
    postgres_available_d1, manifest_files_restored
):
    with _admin_client() as c:
        r = c.get("/api/admin/business-lines/project-management")
    assert r.status_code == 200, r.text
    body = r.json()
    # v1
    assert body["id"] == "project-management"
    assert body["api_prefix"].startswith("/")
    assert "schema" in body["warehouse"]
    # v2
    assert body["has_v2_fields"] is True
    assert set(body["data_scope"]["domains"]) == {
        "business", "finance", "hr", "client", "project",
    }
    assert "finance_bp" in body["owner_role_assignments"]
    assert "fin_bp" in body["access_matrix"]
    assert "fin_view" in body["kpis"]
    assert "hr_view" in body["kpis"]
    assert "shared_view" in body["kpis"]
    # indicators — should be the project-management ones
    assert isinstance(body["indicators"], list)
    assert isinstance(body["charts"], list)
    # kpi items have id + title
    for k in body["kpis"]["fin_view"]:
        assert {"id", "title"}.issubset(k.keys())


# ---------------------------------------------------------------------------
# 4) GET single line — v2 default fill for non-v2 manifests
# ---------------------------------------------------------------------------


def test_get_single_business_line_v2_defaults(
    postgres_available_d1, manifest_files_restored
):
    """residential is a v1-only manifest; the GET should still return
    v2 fields, filled with safe defaults, so the UI can render an
    editor without having to detect missing blocks first."""
    with _admin_client() as c:
        r = c.get("/api/admin/business-lines/residential")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_v2_fields"] is False
    # defaults: all 5 domains
    assert set(body["data_scope"]["domains"]) == {
        "business", "finance", "hr", "client", "project",
    }
    # empty defaults
    assert body["owner_role_assignments"] in ({}, None) or not any(
        body["owner_role_assignments"].values()
    )
    assert body["access_matrix"] in ({}, None) or not any(
        body["access_matrix"].values()
    )
    assert body["kpis"]["fin_view"] == []
    assert body["kpis"]["hr_view"] == []
    assert body["kpis"]["shared_view"] == []


# ---------------------------------------------------------------------------
# 5) PATCH description — round-trip to YAML
# ---------------------------------------------------------------------------


def test_patch_description_writes_to_yaml(
    postgres_available_d1, manifest_files_restored, repo_root
):
    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/residential",
            json={"description": "D1 test description 2026-09-04"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == "D1 test description 2026-09-04"

    # Verify on-disk YAML was actually written
    raw = yaml.safe_load(
        (repo_root / "business_lines" / "residential" / "manifest.yaml")
        .read_text(encoding="utf-8")
    )
    assert raw["description"] == "D1 test description 2026-09-04"


# ---------------------------------------------------------------------------
# 6) PATCH data_scope.domains — round-trip
# ---------------------------------------------------------------------------


def test_patch_data_scope_domains(
    postgres_available_d1, manifest_files_restored, repo_root
):
    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/residential",
            json={"data_scope": {"domains": ["business", "finance", "project"]}},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["data_scope"]["domains"]) == {
        "business", "finance", "project",
    }
    # On-disk verification
    raw = yaml.safe_load(
        (repo_root / "business_lines" / "residential" / "manifest.yaml")
        .read_text(encoding="utf-8")
    )
    assert set(raw["data_scope"]["domains"]) == {
        "business", "finance", "project",
    }


# ---------------------------------------------------------------------------
# 7) PATCH add a KPI — list grows by 1
# ---------------------------------------------------------------------------


def test_patch_kpi_fin_view_adds_item(
    postgres_available_d1, manifest_files_restored, repo_root
):
    # Snapshot project-management's existing fin_view length.
    raw_before = yaml.safe_load(
        (repo_root / "business_lines" / "project-management" / "manifest.yaml")
        .read_text(encoding="utf-8")
    )
    before_count = len(raw_before["kpis"]["fin_view"])
    new_kpi = {
        "id": "d1_test_kpi",
        "title": "D1 Test KPI",
        "source": "mart_test.fct_test",
    }
    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/project-management",
            json={
                "kpis": {
                    "fin_view": raw_before["kpis"]["fin_view"] + [new_kpi],
                    "hr_view": raw_before["kpis"]["hr_view"],
                    "shared_view": raw_before["kpis"]["shared_view"],
                }
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["kpis"]["fin_view"]) == before_count + 1
    assert any(k["id"] == "d1_test_kpi" for k in body["kpis"]["fin_view"])


# ---------------------------------------------------------------------------
# 8) PATCH — reject unknown data domain
# ---------------------------------------------------------------------------


def test_patch_rejects_unknown_data_domain(
    postgres_available_d1, manifest_files_restored
):
    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/residential",
            json={"data_scope": {"domains": ["business", "marketing"]}},
        )
    # FastAPI returns 422 (not 400) for Pydantic body validation errors.
    # This is the OpenAPI/HTTP standard for "schema rejected"; the
    # detail list still pinpoints the bad field, which is what the
    # admin UI needs.
    assert r.status_code == 422, r.text
    body = r.json()
    detail_blob = str(body.get("detail", ""))
    assert "data_scope" in detail_blob or "domains" in detail_blob


# ---------------------------------------------------------------------------
# 9) PATCH — reject access_matrix domain outside the 5
# ---------------------------------------------------------------------------


def test_patch_rejects_bad_access_matrix_domain(
    postgres_available_d1, manifest_files_restored
):
    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/residential",
            json={"access_matrix": {"fin_bp": ["business", "foo"]}},
        )
    assert r.status_code == 422, r.text
    body = r.json()
    detail_blob = str(body.get("detail", ""))
    assert "access_matrix" in detail_blob or "domains" in detail_blob


# ---------------------------------------------------------------------------
# 10) PATCH — reject KPI with bad id
# ---------------------------------------------------------------------------


def test_patch_rejects_bad_kpi_id(
    postgres_available_d1, manifest_files_restored
):
    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/residential",
            json={
                "kpis": {
                    "fin_view": [
                        {"id": "has spaces", "title": "Bad"}
                    ],
                    "hr_view": [],
                    "shared_view": [],
                }
            },
        )
    assert r.status_code == 422, r.text
    body = r.json()
    detail_blob = str(body.get("detail", ""))
    assert "kpi" in detail_blob or "id" in detail_blob


# ---------------------------------------------------------------------------
# 11) Non-admin caller → 403
# ---------------------------------------------------------------------------


def test_get_requires_admin(postgres_available_d1, manifest_files_restored):
    with _nonadmin_client() as c:
        r = c.get("/api/admin/business-lines")
    assert r.status_code == 403, r.text


def test_patch_requires_admin(postgres_available_d1, manifest_files_restored):
    with _nonadmin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/residential",
            json={"description": "hijack"},
        )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# 12) Unknown line_id → 404
# ---------------------------------------------------------------------------


def test_get_unknown_line_returns_404(postgres_available_d1, manifest_files_restored):
    with _admin_client() as c:
        r = c.get("/api/admin/business-lines/does-not-exist")
    assert r.status_code == 404, r.text


def test_patch_unknown_line_returns_404(postgres_available_d1, manifest_files_restored):
    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/does-not-exist",
            json={"description": "x"},
        )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 13) Atomic write — invalid YAML content is rejected without
#     corrupting the existing file
# ---------------------------------------------------------------------------


def test_atomic_write_does_not_corrupt_existing_file(
    postgres_available_d1, manifest_files_restored, repo_root, monkeypatch
):
    """Force the round-trip verifier to reject the next write by
    stubbing ``BusinessLine.model_validate`` to raise. The file on
    disk must be unchanged (or restored from .bak)."""
    from app.routers import admin_business_lines as ab_mod

    manifest_path = repo_root / "business_lines" / "residential" / "manifest.yaml"
    original_content = manifest_path.read_text(encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise ValueError("forced round-trip failure")

    monkeypatch.setattr(ab_mod, "_verify_round_trip", _boom)

    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/residential",
            json={"description": "should not be written"},
        )
    assert r.status_code == 500, r.text
    assert "round-trip" in r.json()["detail"].lower() or "restore" in r.json()["detail"].lower()

    # The file should be either unchanged (the .bak restore copies the
    # pre-test content) or equal to the .bak content. We assert the
    # semantic content: description did NOT change to "should not be written".
    after = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert after["description"] != "should not be written"
    # If the test snapshot fixture put the .bak file back, the file
    # is the original. If the restore from .bak copied the pre-test
    # snapshot, the description is the original. Either way, the
    # file is consistent — never a half-written mess.
    bak = manifest_path.with_suffix(manifest_path.suffix + ".bak")
    if bak.exists():
        # The .bak was created by the atomic-write step BEFORE the
        # round-trip failed, so it should hold the original content.
        bak_content = yaml.safe_load(bak.read_text(encoding="utf-8"))
        assert bak_content["description"] == after["description"]


# ---------------------------------------------------------------------------
# 14) Backup — .bak is created before each successful write
# ---------------------------------------------------------------------------


def test_backup_created_on_successful_write(
    postgres_available_d1, manifest_files_restored, repo_root
):
    manifest_path = repo_root / "business_lines" / "residential" / "manifest.yaml"
    bak = manifest_path.with_suffix(manifest_path.suffix + ".bak")

    # Snapshot the original content for comparison.
    original = manifest_path.read_bytes()

    # The .bak may or may not exist before this test (the
    # restore fixture may have left one behind from a previous test).
    bak_existed_before = bak.exists()

    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/residential",
            json={"description": "D1 backup test"},
        )
    assert r.status_code == 200, r.text

    # .bak must now exist and hold the ORIGINAL content.
    assert bak.exists()
    bak_content = bak.read_bytes()
    assert bak_content == original, "manifest.yaml.bak should hold the pre-write content"


# ---------------------------------------------------------------------------
# 15) Lru_cache hot-reload — the new value is visible to a fresh request
# ---------------------------------------------------------------------------


def test_lru_cache_reload_after_patch(
    postgres_available_d1, manifest_files_restored
):
    with _admin_client() as c:
        # 1. PATCH
        r = c.patch(
            "/api/admin/business-lines/retail",
            json={"description": "D1 cache reload test"},
        )
        assert r.status_code == 200, r.text
        # 2. GET back — must see the new value (cache was cleared)
        r = c.get("/api/admin/business-lines/retail")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["description"] == "D1 cache reload test"


# ---------------------------------------------------------------------------
# 16) Empty PATCH body — returns the current state without writing
# ---------------------------------------------------------------------------


def test_empty_patch_is_noop(
    postgres_available_d1, manifest_files_restored
):
    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/retail",
            json={},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "retail"


# ---------------------------------------------------------------------------
# 17) PATCH with indicators (full replacement)
# ---------------------------------------------------------------------------


def test_patch_indicators_replaces_list(
    postgres_available_d1, manifest_files_restored, repo_root
):
    new_indicators = [
        {
            "id": "d1_test_indicator",
            "title": "D1 Test Indicator",
            "unit": "%",
            "format": "percent",
            "aggregation": "avg",
            "source": "mart_test.fct_test",
            "description": "tested by D1",
        }
    ]
    with _admin_client() as c:
        r = c.patch(
            "/api/admin/business-lines/residential",
            json={"indicators": new_indicators},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["indicators"]) == 1
    assert body["indicators"][0]["id"] == "d1_test_indicator"

    # Verify on disk
    ind_path = repo_root / "business_lines" / "residential" / "indicators.yaml"
    raw = yaml.safe_load(ind_path.read_text(encoding="utf-8"))
    assert len(raw["indicators"]) == 1
    assert raw["indicators"][0]["id"] == "d1_test_indicator"
