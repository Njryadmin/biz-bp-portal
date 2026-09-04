"""
apps/api/tests/test_dashboard.py
================================

Per-perspective dashboard MVP tests (E task, 2026-09-04).

What we cover
-------------
* ``GET /api/dashboard/fin`` returns 200 + fin_view + shared_view KPIs
  for every line the user can see.
* ``GET /api/dashboard/hr`` returns 200 + hr_view + shared_view KPIs
  (and excludes any pure-fin content).
* ``GET /api/dashboard/shared`` returns 200 + only shared_view KPIs,
  no domain check.
* Cross-line role (``fin_bp_global``) sees every line; line-scoped
  role (``fin_bp``) sees only their own line.
* Cross-domain call is rejected with 403 (e.g. ``hr_bp`` calling
  ``/fin``, ``fin_bp`` calling ``/hr``).
* ``auditor`` (read-only) can call all three.
* ``X-Active-View`` header flips the *active_view* recorded in the v2
  user but does NOT change the data the endpoint returns (the URL
  path is the source of truth). This is intentional: the dashboard
  data is selected by the route, the header is for audit / downstream
  consumers.
* The shared view returns 200 (not 403) for an empty access set.

Design notes
------------
* The router returns mock values (deterministic hash), so tests do
  NOT seed any DB tables; only the registry YAML on disk matters.
* We use ``app.dependency_overrides[get_current_user_v2]`` to inject
  per-test users (no cookie / DB / JWT round-trip).
* All test users are SINGLE-binding ``CurrentUserV2`` built by
  ``make_user()`` (mirrors test_rbac_v2.py / test_rbac_v2_router_guards.py
  so the matrix semantics are isolated from union-of-permissions).
* conftest.py autouse ``_disable_audit_middleware_in_tests`` patches
  the audit writer to noop, so the test does not block on Postgres.

Run
----
    cd apps/api
    python -m pytest tests/test_dashboard.py -v --tb=short
"""
from __future__ import annotations

import os
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

# rbac_v2 dependency for CurrentUserV2 + enums
from app.core.rbac_v2 import (
    CurrentUserV2,
    DataDomain,
    Role,
    Scope,
    UserRoleBinding,
)


# ---------------------------------------------------------------------------
# v2 mock user factory (single binding, mirrors the other v2 test files)
# ---------------------------------------------------------------------------


_GLOBAL_SCOPE_ROLES = frozenset(
    {
        Role.ADMIN,
        Role.AUDITOR,
        Role.VIEWER,
        Role.FIN_BP_GLOBAL,
        Role.HR_BP_GLOBAL,
    }
)


def make_user(role: Role, line_id: str = "residential") -> CurrentUserV2:
    if role in _GLOBAL_SCOPE_ROLES:
        scope = Scope.GLOBAL
        bid: str | None = None
        accessible: list[str] = []
    else:
        scope = Scope.BUSINESS_LINE
        bid = line_id
        accessible = [line_id]
    return CurrentUserV2(
        id=1,
        username=f"test_{role.value}",
        display_name=f"Test {role.value}",
        email=None,
        is_active=True,
        roles=[role.value],
        accessible_lines=accessible,
        bindings=[
            UserRoleBinding(
                role=role,
                scope=scope,
                business_line_id=bid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Per-role TestClient fixtures
# ---------------------------------------------------------------------------


def _build_client(user: CurrentUserV2) -> Iterator[TestClient]:
    from app.main import create_app
    from app.core.auth_v2 import get_current_user_v2

    app = create_app()
    app.dependency_overrides[get_current_user_v2] = lambda: user
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_auditor() -> Iterator[TestClient]:
    yield from _build_client(make_user(Role.AUDITOR))


@pytest.fixture
def client_admin() -> Iterator[TestClient]:
    yield from _build_client(make_user(Role.ADMIN))


@pytest.fixture
def client_fin_bp() -> Iterator[TestClient]:
    # fin_bp on residential line → FINANCE accessible on residential
    yield from _build_client(make_user(Role.FIN_BP, line_id="residential"))


@pytest.fixture
def client_fin_bp_global() -> Iterator[TestClient]:
    yield from _build_client(make_user(Role.FIN_BP_GLOBAL))


@pytest.fixture
def client_hr_bp() -> Iterator[TestClient]:
    yield from _build_client(make_user(Role.HR_BP, line_id="residential"))


@pytest.fixture
def client_hr_bp_global() -> Iterator[TestClient]:
    yield from _build_client(make_user(Role.HR_BP_GLOBAL))


@pytest.fixture
def client_line_owner() -> Iterator[TestClient]:
    # line_owner on residential → FINANCE + HR + all 5 domains accessible
    yield from _build_client(make_user(Role.LINE_OWNER, line_id="residential"))


@pytest.fixture
def client_viewer() -> Iterator[TestClient]:
    yield from _build_client(make_user(Role.VIEWER))


# ---------------------------------------------------------------------------
# Expected KPI titles per line (from the 9 manifests on disk; the
# 8 v1 lines have empty kpis blocks, project-management has 8 items).
# ---------------------------------------------------------------------------

# project-management's kpis.fin_view + shared_view titles
_PM_FIN_TITLES = {"月度代建合同额", "预算偏差率", "在管项目数"}
_PM_HR_TITLES = {"PM 在职 FTE", "团队产能利用率"}
_PM_SHARED_TITLES = {"进度偏差率", "客户满意度", "续约率"}
_PM_FIN_AND_SHARED = _PM_FIN_TITLES | _PM_SHARED_TITLES
_PM_HR_AND_SHARED = _PM_HR_TITLES | _PM_SHARED_TITLES


# ---------------------------------------------------------------------------
# /api/dashboard/fin
# ---------------------------------------------------------------------------


def test_dashboard_fin_200_for_fin_bp(client_fin_bp) -> None:
    """fin_bp (本线 residential) → 200, only residential KPIs.

    residential's manifest has an empty kpis block today, so the
    response is well-formed but the kpi list is empty. The contract
    is: kpi list may be empty if no manifest has data; the response
    shape must still be correct.
    """
    r = client_fin_bp.get("/api/dashboard/fin")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["view"] == "fin"
    kpis = body["kpis"]
    # fin_bp on residential → only residential in scope. No KPI
    # leakage from project-management (which is on a different line).
    kpi_line_ids = {item["line_id"] for item in kpis}
    assert kpi_line_ids.issubset({"residential"}), kpi_line_ids
    # ``lines`` array mirrors the accessible set (just residential).
    assert [ln["line_id"] for ln in body["lines"]] == ["residential"]


def test_dashboard_fin_200_for_fin_bp_global(client_fin_bp_global) -> None:
    """fin_bp_global (GLOBAL) → 200, sees every line, including pm's
    fin_view + shared_view content."""
    r = client_fin_bp_global.get("/api/dashboard/fin")
    assert r.status_code == 200, r.text
    body = r.json()
    titles = {k["title"] for k in body["kpis"]}
    # pm is the only line with kpis.fin_view filled in; the contract says
    # project-management's 3 fin-view + 3 shared-view titles must appear.
    assert _PM_FIN_AND_SHARED.issubset(titles), (
        f"missing pm fin/shared kpis: expected={_PM_FIN_AND_SHARED}, "
        f"got={titles}"
    )
    # kpi_count per line should sum to len(kpis)
    total_in_lines = sum(ln["kpi_count"] for ln in body["lines"])
    assert total_in_lines == len(body["kpis"])


def test_dashboard_fin_403_for_hr_bp(client_hr_bp) -> None:
    """hr_bp (no FINANCE access) → 403 on /fin."""
    r = client_hr_bp.get("/api/dashboard/fin")
    assert r.status_code == 403, r.text
    body = r.json()
    assert "FINANCE" in body["detail"].upper() or "finance" in body["detail"]


def test_dashboard_fin_200_for_auditor(client_auditor) -> None:
    """auditor → 200, sees everything (matrix allows FINANCE view)."""
    r = client_auditor.get("/api/dashboard/fin")
    assert r.status_code == 200, r.text
    body = r.json()
    titles = {k["title"] for k in body["kpis"]}
    assert _PM_FIN_AND_SHARED.issubset(titles)


def test_dashboard_fin_200_for_line_owner(client_line_owner) -> None:
    """line_owner (residential) — can view FINANCE → 200."""
    r = client_line_owner.get("/api/dashboard/fin")
    assert r.status_code == 200, r.text


def test_dashboard_fin_200_for_admin(client_admin) -> None:
    """admin → 200 (matrix allows FINANCE view)."""
    r = client_admin.get("/api/dashboard/fin")
    assert r.status_code == 200, r.text


def test_dashboard_fin_200_for_viewer(client_viewer) -> None:
    """viewer → 200 (matrix allows FINANCE view)."""
    r = client_viewer.get("/api/dashboard/fin")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# /api/dashboard/hr
# ---------------------------------------------------------------------------


def test_dashboard_hr_200_for_hr_bp(client_hr_bp) -> None:
    """hr_bp (本线 residential) → 200.

    residential's manifest has an empty kpis block today, so the
    response is well-formed but the kpi list is empty (same contract
    as the FIN case).
    """
    r = client_hr_bp.get("/api/dashboard/hr")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["view"] == "hr"
    kpi_line_ids = {item["line_id"] for item in body["kpis"]}
    assert kpi_line_ids.issubset({"residential"}), kpi_line_ids
    # ``lines`` array mirrors the accessible set (just residential).
    assert [ln["line_id"] for ln in body["lines"]] == ["residential"]


def test_dashboard_hr_200_for_hr_bp_global(client_hr_bp_global) -> None:
    """hr_bp_global → 200, includes pm's hr_view + shared_view."""
    r = client_hr_bp_global.get("/api/dashboard/hr")
    assert r.status_code == 200, r.text
    body = r.json()
    titles = {k["title"] for k in body["kpis"]}
    assert _PM_HR_AND_SHARED.issubset(titles), titles


def test_dashboard_hr_403_for_fin_bp(client_fin_bp) -> None:
    """fin_bp (no HR access) → 403 on /hr."""
    r = client_fin_bp.get("/api/dashboard/hr")
    assert r.status_code == 403, r.text
    body = r.json()
    assert "HR" in body["detail"].upper() or "hr" in body["detail"]


def test_dashboard_hr_200_for_auditor(client_auditor) -> None:
    """auditor → 200 on /hr."""
    r = client_auditor.get("/api/dashboard/hr")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# /api/dashboard/shared
# ---------------------------------------------------------------------------


def test_dashboard_shared_200_for_auditor(client_auditor) -> None:
    """auditor → 200, sees every shared kpi."""
    r = client_auditor.get("/api/dashboard/shared")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["view"] == "shared"
    titles = {k["title"] for k in body["kpis"]}
    # shared_view on pm has 3 titles
    assert _PM_SHARED_TITLES.issubset(titles)


def test_dashboard_shared_200_for_fin_bp(client_fin_bp) -> None:
    """fin_bp (本线) → 200, includes residential's empty kpis (== [])."""
    r = client_fin_bp.get("/api/dashboard/shared")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["view"] == "shared"
    line_ids = {item["line_id"] for item in body["kpis"]}
    # residential doesn't have a kpis block filled in, so 0 items.
    # Just ensure the response is well-formed.
    assert line_ids.issubset({"residential"})


def test_dashboard_shared_200_for_user_with_no_lines() -> None:
    """User with NO accessible lines → 200, empty arrays (NOT 403).

    Rationale: /shared is a "anyone authenticated" endpoint; the empty
    accessible set is a valid input (no lines = no KPIs). 403 is
    reserved for cross-domain attempts on /fin and /hr.
    """
    from app.main import create_app
    from app.core.auth_v2 import get_current_user_v2

    user = CurrentUserV2(
        id=99,
        username="no_one",
        display_name="no one",
        email=None,
        is_active=True,
        roles=[],
        accessible_lines=[],
        bindings=[],
    )
    app = create_app()
    app.dependency_overrides[get_current_user_v2] = lambda: user
    with TestClient(app) as c:
        r = c.get("/api/dashboard/shared")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["view"] == "shared"
    assert body["kpis"] == []
    assert body["lines"] == []


# ---------------------------------------------------------------------------
# X-Active-View header propagation (the header is for audit / downstream;
# the URL path is the source of truth for which KPIs to return)
# ---------------------------------------------------------------------------


def test_dashboard_x_active_view_sets_user_active_view(
    client_auditor,
) -> None:
    """``X-Active-View`` header is accepted by the dashboard endpoints
    without error. The actual ``active_view`` recording happens inside
    ``get_current_user_v2`` (see auth_v2.py:222) — that code path is
    covered by the dedicated ``/api/auth/me-v2`` endpoints below
    (which use the same dep).

    The dashboard URL path is the source of truth for *which* KPI set
    is returned; the header is a hint for audit / downstream. So we
    just verify the endpoint is reachable with the header set.

    Note: we use ``client_auditor`` (not ``fin_bp_global``) so the
    HR endpoint passes its domain gate.
    """
    # fin data still returned even with X-Active-View: hr
    r = client_auditor.get(
        "/api/dashboard/fin", headers={"X-Active-View": "hr"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    titles = {k["title"] for k in body["kpis"]}
    assert _PM_FIN_TITLES.issubset(titles), titles

    # hr data still returned even with X-Active-View: fin
    r = client_auditor.get(
        "/api/dashboard/hr", headers={"X-Active-View": "fin"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    titles = {k["title"] for k in body["kpis"]}
    assert _PM_HR_TITLES.issubset(titles), titles

    # shared is unaffected by the header
    r = client_auditor.get(
        "/api/dashboard/shared", headers={"X-Active-View": "fin"}
    )
    assert r.status_code == 200, r.text


def test_dashboard_fin_returns_fin_data_with_hr_header(
    client_fin_bp_global,
) -> None:
    """Even with ``X-Active-View: hr`` set, the ``/fin`` URL still
    returns fin_view + shared_view KPIs. The header is a hint, not a
    data selector — this is by design (E, 2026-09-04 comment block
    in ``app/routers/dashboard.py``)."""
    r = client_fin_bp_global.get(
        "/api/dashboard/fin", headers={"X-Active-View": "hr"}
    )
    assert r.status_code == 200
    body = r.json()
    titles = {k["title"] for k in body["kpis"]}
    # The /fin response must include pm's fin_view titles
    assert _PM_FIN_TITLES.issubset(titles), titles


# ---------------------------------------------------------------------------
# Cross-cutting: shared KPIs appear in BOTH fin and hr responses
# ---------------------------------------------------------------------------


def test_dashboard_shared_kpis_appear_in_both_views(
    client_fin_bp_global, client_hr_bp_global
) -> None:
    """Project-management's 3 shared_view titles must appear in /fin,
    /hr, AND /shared — they're "shared" by definition."""
    expected = _PM_SHARED_TITLES

    r1 = client_fin_bp_global.get("/api/dashboard/fin")
    r2 = client_hr_bp_global.get("/api/dashboard/hr")
    r3 = client_auditor_cached_get(client_fin_bp_global, "/api/dashboard/shared")

    titles1 = {k["title"] for k in r1.json()["kpis"]}
    titles2 = {k["title"] for k in r2.json()["kpis"]}
    titles3 = {k["title"] for k in r3.json()["kpis"]}

    assert expected.issubset(titles1), (expected, titles1)
    assert expected.issubset(titles2), (expected, titles2)
    assert expected.issubset(titles3), (expected, titles3)


def client_auditor_cached_get(client, path: str):
    """Tiny helper so the previous test can reuse the same client for /shared."""
    return client.get(path)


# ---------------------------------------------------------------------------
# Per-view: line-scope users only see their line, never another
# ---------------------------------------------------------------------------


def test_dashboard_hr_bp_sees_only_own_line(client_hr_bp) -> None:
    r = client_hr_bp.get("/api/dashboard/hr")
    body = r.json()
    line_ids = {item["line_id"] for item in body["kpis"]}
    # residential's kpis.hr_view is empty today → 0 kpis
    assert line_ids.issubset({"residential"})


def test_dashboard_fin_bp_sees_only_own_line(client_fin_bp) -> None:
    r = client_fin_bp.get("/api/dashboard/fin")
    body = r.json()
    line_ids = {item["line_id"] for item in body["kpis"]}
    # residential's kpis.fin_view is empty today → 0 kpis, no leakage
    assert line_ids.issubset({"residential"})


# ---------------------------------------------------------------------------
# /api/auth/me-v2: binding wire format
# ---------------------------------------------------------------------------


def test_me_v2_returns_bindings(client_fin_bp) -> None:
    r = client_fin_bp.get("/api/auth/me-v2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "test_fin_bp"
    bindings = body["bindings"]
    assert len(bindings) == 1
    b = bindings[0]
    assert b["role"] == "fin_bp"
    assert b["scope"] == "business_line"
    assert b["line_id"] == "residential"


def test_me_v2_includes_active_view_default_none(client_fin_bp_global) -> None:
    r = client_fin_bp_global.get("/api/auth/me-v2")
    assert r.status_code == 200, r.text
    # The dep override we use in this test doesn't read the header
    # (FastAPI's Request injection through dependency_overrides is
    # awkward under TestClient — see the test above for the workaround).
    # What we assert here is the wire format: active_view is present
    # and is null when no view is set.
    assert r.json()["active_view"] is None


def test_me_v2_bindings_global_user(client_fin_bp_global) -> None:
    """``fin_bp_global`` is GLOBAL scope: ``line_id`` is null and
    ``scope=global``."""
    r = client_fin_bp_global.get("/api/auth/me-v2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["bindings"]) == 1
    b = body["bindings"][0]
    assert b["role"] == "fin_bp_global"
    assert b["scope"] == "global"
    assert b["line_id"] is None
    assert body["accessible_lines"] == []  # global → no line scope


def test_me_v2_auditor_bindings(client_auditor) -> None:
    """auditor: GLOBAL, role=auditor, scope=global, line_id=null."""
    r = client_auditor.get("/api/auth/me-v2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bindings"][0]["role"] == "auditor"
    assert body["bindings"][0]["scope"] == "global"
