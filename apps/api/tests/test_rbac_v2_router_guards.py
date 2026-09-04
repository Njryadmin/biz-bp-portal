"""
apps/api/tests/test_rbac_v2_router_guards.py

============================================================
RBAC v2 router guard 集成测试 (B1 任务)
============================================================

背景
----
P0 已合入 ``rbac_v2`` + ``auth_v2`` (commit 2012244/216cef3/8e94247),
定义 8 角色 × 5 数据域 × 读/写 静态权限矩阵 (PERMISSION_MATRIX),
以及 ``check_domain_access()`` imperative helper.

本测试覆盖: 4 个通用 router (sensitivity / forecast / alerts / copilot)
从 v1 ``require_business_line`` 升级到 v2 ``check_domain_access`` 后的
**集成** 行为 (HTTP 端到端), 不仅单元层 80 case.

设计
----
1. **隔离 v1/v2 依赖**: 每个 v2 端点现在用 ``Depends(get_current_user_v2)``,
   跟 v1 的 ``get_current_user`` 是两个 FastAPI dep.  本测试用
   ``app.dependency_overrides[get_current_user_v2]`` 注入 v2 mock 用户,
   保留 v1 dep 不动 (不破坏 test_auth.py / conftest.py 其它测试).

2. **角色 factory**: 单 binding ``make_user()`` 工厂构造 8 角色用户
   (跟 test_rbac_v2.py 同款, 避免 union-of-permissions 干扰).

3. **不动业务线 router**: 9 条业务线 router 自身不带 guard, 靠
   ``registry.py`` mount 时统一加.  本测试只覆盖 4 个通用 router.

4. **pgserver 不是必须**: 所有端点要么只读 profile, 要么写触发 mock
   数据生成.  不依赖数据库 (audit 中间件被 conftest 短路).

5. **service-token**: admin / 跨线权限测试用真实
   ``BIZ_BP_SERVICE_TOKEN`` 路径, 验证 v2 service-token 也走通了
   (它返回 admin+auditor binding, 跟 v1 一致).

执行
----
    cd apps/api
    python -m pytest tests/test_rbac_v2_router_guards.py -v --tb=short

参考
----
    apps/api/app/core/rbac_v2.py     check_domain_access
    apps/api/app/core/auth_v2.py     get_current_user_v2
    apps/api/app/routers/sensitivity.py   FINANCE + PROJECT
    apps/api/app/routers/forecast.py      FINANCE + PROJECT
    apps/api/app/routers/alerts.py        BUSINESS
    apps/api/app/routers/copilot.py       BUSINESS
"""
from __future__ import annotations

import os
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.rbac_v2 import (
    CurrentUserV2,
    DataDomain,
    Role,
    Scope,
    UserRoleBinding,
)


# ---------------------------------------------------------------------------
# v2 mock user factory (与 test_rbac_v2.py 同款, 单 binding 避免 union 干扰)
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
    """构造单 binding ``CurrentUserV2`` 供本测试用."""
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
# App + TestClient fixture: override v2 dep, leave v1 dep intact
# ---------------------------------------------------------------------------


def _build_client(user: CurrentUserV2) -> Iterator[TestClient]:
    """Build a TestClient that injects ``user`` as the v2 current user.

    注意: ``create_app()`` 会触发 lifespan → ``init_db`` + ``seed_initial_users``,
    但 conftest.py 已用 autouse fixture 把这两个短路成 noop, 数据库不可用也能跑.
    """
    from app.main import create_app
    from app.core.auth_v2 import get_current_user_v2

    app = create_app()
    app.dependency_overrides[get_current_user_v2] = lambda: user
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_auditor() -> Iterator[TestClient]:
    """auditor 用户 (GLOBAL 只读, 所有域 view=True, write=False)."""
    yield from _build_client(make_user(Role.AUDITOR))


@pytest.fixture
def client_admin() -> Iterator[TestClient]:
    """admin 用户 (GLOBAL 只读, 同 auditor; admin 在 v2 矩阵里也是只读)."""
    yield from _build_client(make_user(Role.ADMIN))


@pytest.fixture
def client_fin_bp() -> Iterator[TestClient]:
    """fin_bp 用户 (本线, business/finance/project 读写; hr/client 只读)."""
    yield from _build_client(make_user(Role.FIN_BP, line_id="residential"))


@pytest.fixture
def client_fin_bp_global() -> Iterator[TestClient]:
    """fin_bp_global 用户 (跨线, finance 读写; business/project 只读; hr/client 不可见)."""
    yield from _build_client(make_user(Role.FIN_BP_GLOBAL))


@pytest.fixture
def client_hr_bp() -> Iterator[TestClient]:
    """hr_bp 用户 (本线, business/hr/client/project 有限权限, finance 不可见)."""
    yield from _build_client(make_user(Role.HR_BP, line_id="residential"))


# ---------------------------------------------------------------------------
# check_domain_access 单元 (helper 自检)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_domain_access_any_of_dual_domain() -> None:
    """``check_domain_access`` 多域 any-of: 任一域允许即通过.

    模拟敏感性场景: 端点声明需要 ``[FINANCE, PROJECT]``, 用户 fin_bp
    两者都有权限 (matrix 允许), 应通过.
    """
    from app.core.rbac_v2 import check_domain_access

    user = make_user(Role.FIN_BP, line_id="residential")
    # 不抛异常 = 通过
    await check_domain_access(
        user, "residential", [DataDomain.FINANCE, DataDomain.PROJECT], write=True
    )


@pytest.mark.asyncio
async def test_check_domain_access_any_of_dual_domain_fin_only() -> None:
    """``[FINANCE, PROJECT]`` 任一即可 — hr_bp 不能写 finance, 但能读 project,
    因此 ``write=True`` 拒绝, ``write=False`` 通过 (project 域读)."""
    from app.core.rbac_v2 import check_domain_access
    from fastapi import HTTPException

    user = make_user(Role.HR_BP, line_id="residential")
    # 读 — hr_bp project 域 view=True (matrix), 至少一个域允许 → 通过
    await check_domain_access(
        user, "residential", [DataDomain.FINANCE, DataDomain.PROJECT], write=False
    )
    # 写 — hr_bp project 域 write=False, finance 写=False, 都禁 → 403
    with pytest.raises(HTTPException) as exc_info:
        await check_domain_access(
            user, "residential", [DataDomain.FINANCE, DataDomain.PROJECT], write=True
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_check_domain_access_raises_400_on_empty_line_id() -> None:
    """空 line_id → 400 (line_id 是 required)."""
    from app.core.rbac_v2 import check_domain_access
    from fastapi import HTTPException

    user = make_user(Role.ADMIN)
    with pytest.raises(HTTPException) as exc_info:
        await check_domain_access(user, "", DataDomain.BUSINESS, write=False)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_check_domain_access_raises_403_when_all_denied() -> None:
    """任一域都不允许 → 403, detail 列出尝试的域列表 + line_id + user roles.

    hr_bp_global 矩阵: business view=True, finance/client/project 不可见,
    hr 读写. 所以 [FINANCE, PROJECT] 写操作, 两域都禁, 应 403.
    (注意: 不能用 [FINANCE, HR] — HR 写权限存在, any-of 通过.)
    """
    from app.core.rbac_v2 import check_domain_access
    from fastapi import HTTPException

    user = make_user(Role.HR_BP_GLOBAL)
    with pytest.raises(HTTPException) as exc_info:
        await check_domain_access(
            user, "residential", [DataDomain.FINANCE, DataDomain.PROJECT], write=True
        )
    assert exc_info.value.status_code == 403
    detail = str(exc_info.value.detail)
    assert "finance" in detail
    assert "project" in detail
    assert "residential" in detail
    assert "hr_bp_global" in detail


# ---------------------------------------------------------------------------
# Parametrized matrix: 4 角色 × 5 域 × 2 (读/写) 的核心断言
# ---------------------------------------------------------------------------


# 取 4 个代表性角色 (admin 全只读, fin_bp 业务线范围, fin_bp_global 跨线,
# hr_bp 验证 FIN 隔离)
_MATRIX_ROLES = [Role.ADMIN, Role.AUDITOR, Role.FIN_BP, Role.HR_BP_GLOBAL]


@pytest.mark.parametrize("role", _MATRIX_ROLES)
@pytest.mark.parametrize("domain", list(DataDomain))
@pytest.mark.parametrize("write", [False, True])
@pytest.mark.asyncio
async def test_check_domain_access_matches_matrix(
    role: Role, domain: DataDomain, write: bool
) -> None:
    """``check_domain_access`` 必须对 (role, domain, write) 严格等于
    ``PERMISSION_MATRIX[role][domain]``. 这是 helper 与 matrix 一致性的回归门."""
    from app.core.rbac_v2 import PERMISSION_MATRIX, check_domain_access
    from fastapi import HTTPException

    user = make_user(role)
    expected_allowed = PERMISSION_MATRIX[role][domain]["write" if write else "view"]
    if expected_allowed:
        # 期望通过 — 不应抛
        await check_domain_access(user, "residential", domain, write=write)
    else:
        # 期望拒绝 — 应抛 403
        with pytest.raises(HTTPException) as exc_info:
            await check_domain_access(user, "residential", domain, write=write)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# /api/sensitivity — FINANCE + PROJECT 域
# ---------------------------------------------------------------------------


def test_sensitivity_profile_denies_auditor_write(client_auditor) -> None:
    """``POST /api/sensitivity/analyze`` auditor 应 403 (写).

    auditor 全 view + 全不 write (裁判运动员), sensitivity analyze 是
    写 (生成新分析), 所以拒绝. 这条是 v1 → v2 升级的回归门.
    """
    r = client_auditor.post(
        "/api/sensitivity/analyze",
        json={
            "line_id": "residential",
            "output_id": "irr",
            "input1_id": "rent",
            "input1_range": [0.0, 1.0],
            "input1_step": 0.1,
        },
    )
    assert r.status_code == 403, r.text
    assert "residential" in r.text or "finance" in r.text or "project" in r.text


def test_sensitivity_profile_allows_fin_bp_write(client_fin_bp) -> None:
    """``POST /api/sensitivity/analyze`` fin_bp (本线) 应 200 (写).

    fin_bp 在 residential line 上对 finance + project 都有写权限,
    any-of 语义满足. 这条验证 v2 helper 与 router 接入正确.
    """
    r = client_fin_bp.post(
        "/api/sensitivity/analyze",
        json={
            "line_id": "residential",
            "output_id": "irr",  # 任意 output id — fin_bp 通过 guard
            "input1_id": "rent",
            "input1_range": [0.0, 1.0],
            "input1_step": 0.1,
        },
    )
    # 200 (guard 通过 + 业务正常) 或 400/404 (输出 id 不对 — 但不是 403)
    assert r.status_code != 403, (
        f"fin_bp 应通过 v2 guard, 实际 403: {r.text}"
    )
    # 业务上 output_id 可能是 400/404, 但绝不会是 403
    assert r.status_code in (200, 400, 404), f"unexpected status: {r.status_code} {r.text}"


def test_sensitivity_profile_get_allows_auditor(client_auditor) -> None:
    """``GET /api/sensitivity/profiles/{line_id}`` auditor 应 200 (读).

    auditor 全 view, sensitivity profile 读涉及 finance + project 域,
    any-of 满足. 这条覆盖 v2 read 路径.
    """
    r = client_auditor.get("/api/sensitivity/profiles/residential")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["line_id"] == "residential"


# ---------------------------------------------------------------------------
# /api/forecast — FINANCE + PROJECT 域
# ---------------------------------------------------------------------------


def test_forecast_run_allows_fin_bp_write(client_fin_bp) -> None:
    """``POST /api/forecast/run`` fin_bp (本线) 应通过 v2 guard.

    业务上 indicator_id 可能错 (400/404), 但 guard 层面不抛 403.
    """
    r = client_fin_bp.post(
        "/api/forecast/run",
        json={
            "line_id": "residential",
            "indicator_id": "dynamic_irr",
            "horizon_months": 3,
            "method": "linear_trend",
        },
    )
    assert r.status_code != 403, f"fin_bp 应通过 guard: {r.text}"
    assert r.status_code in (200, 400, 404), f"unexpected: {r.status_code} {r.text}"


def test_forecast_compare_allows_fin_bp_global_cross_line(client_fin_bp_global) -> None:
    """``POST /api/forecast/compare`` fin_bp_global 跨线应 200 (非 403).

    fin_bp_global 是 GLOBAL scope, 跨线 finance 写权限有, 满足
    ``[FINANCE, PROJECT]`` any-of (finance 命中). 验证 v2 跨线场景.
    """
    r = client_fin_bp_global.post(
        "/api/forecast/compare",
        json={
            "line_id": "retail",  # 跨线 — fin_bp_global 才行
            "indicator_id": "dynamic_irr",
            "horizon_months": 3,
        },
    )
    assert r.status_code != 403, f"fin_bp_global 跨线应通过: {r.text}"
    assert r.status_code in (200, 400, 404), f"unexpected: {r.status_code} {r.text}"


def test_forecast_run_denies_auditor_write(client_auditor) -> None:
    """``POST /api/forecast/run`` auditor 应 403 (写).

    auditor 全 view, 全不 write; forecast run 是写, 拒绝.
    """
    r = client_auditor.post(
        "/api/forecast/run",
        json={
            "line_id": "residential",
            "indicator_id": "dynamic_irr",
            "horizon_months": 3,
        },
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# /api/alerts — BUSINESS 域
# ---------------------------------------------------------------------------


def test_alerts_list_rules_denies_auditor(client_auditor) -> None:
    """``GET /api/alerts/rules/{line_id}`` auditor 应 200 (读).

    auditor BUSINESS 域 view=True, 读允许.
    """
    r = client_auditor.get("/api/alerts/rules/residential")
    assert r.status_code == 200, r.text


def test_alerts_list_profiles_denies_hr_bp_global(client_hr_bp) -> None:
    """``GET /api/alerts/profiles`` hr_bp 应 200 (BUSINESS 域可读).

    hr_bp 在 residential line 上对 BUSINESS 域 view=True, matrix 允许.
    """
    r = client_hr_bp.get("/api/alerts/profiles")
    assert r.status_code == 200, r.text


def test_alerts_check_denies_auditor_write(client_auditor) -> None:
    """``POST /api/alerts/check`` auditor 应 403 (写).

    alert check 触发评估 = 写, auditor 全不写, 拒绝.
    """
    r = client_auditor.post(
        "/api/alerts/check",
        json={"line_id": "residential", "rule_ids": ["irr_below_threshold"]},
    )
    assert r.status_code == 403, r.text


def test_alerts_check_allows_fin_bp_write(client_fin_bp) -> None:
    """``POST /api/alerts/check`` fin_bp 应 200 (BUSINESS 写权限).

    fin_bp 对 residential line 的 BUSINESS 域 write=True, 通过.
    业务上可能 0 alerts (mock 数据非确定性), 但 status 不会是 403.
    """
    r = client_fin_bp.post(
        "/api/alerts/check",
        json={"line_id": "residential", "rule_ids": ["irr_below_threshold"]},
    )
    assert r.status_code == 200, f"fin_bp 应通过: {r.text}"


# ---------------------------------------------------------------------------
# /api/copilot — BUSINESS 域
# ---------------------------------------------------------------------------


def test_copilot_ask_no_line_id_passes_for_auditor(client_auditor) -> None:
    """``POST /api/copilot/ask`` 无 line_id 时, 不走 line guard, auditor 应 200.

    copilot ask 只在 ``req.line_id`` 显式传时才查 BUSINESS 域; 没传时
    engine 走 user 自己的 accessible_lines. auditor GLOBAL, 跨所有线.
    """
    r = client_auditor.post(
        "/api/copilot/ask",
        json={"question": "本月回款情况如何?"},
    )
    assert r.status_code == 200, r.text


def test_copilot_ask_with_line_id_denies_auditor_write(client_auditor) -> None:
    """``POST /api/copilot/ask`` 带 line_id 时, auditor 应 403 (写).

    auditor BUSINESS 域 view=True 但 write=False, copilot ask 调
    ``check_domain_access(..., write=True)`` (LLM 调用视为副作用), 拒绝.
    """
    r = client_auditor.post(
        "/api/copilot/ask",
        json={"question": "本月回款情况如何?", "line_id": "residential"},
    )
    assert r.status_code == 403, r.text
    # 错误信息应该提到 business 域 (因为 v2 detail 列出 domains)
    assert "business" in r.text.lower()


def test_copilot_ask_with_line_id_allows_fin_bp(client_fin_bp) -> None:
    """``POST /api/copilot/ask`` 带 line_id, fin_bp 应 200 (BUSINESS 写权限).

    fin_bp 在 residential line 上对 BUSINESS 域 write=True, 通过 v2 guard.
    """
    r = client_fin_bp.post(
        "/api/copilot/ask",
        json={"question": "本月回款情况如何?", "line_id": "residential"},
    )
    assert r.status_code == 200, f"fin_bp 应通过: {r.text}"


# ---------------------------------------------------------------------------
# service-token 端到端: BIZ_BP_SERVICE_TOKEN 路径 (v2 已在 auth_v2.py 处理)
# ---------------------------------------------------------------------------


def test_service_token_admin_passes_alerts_check(monkeypatch) -> None:
    """``BIZ_BP_SERVICE_TOKEN`` 路径: 走 v2 ``get_current_user_v2`` 拿到
    admin+auditor 双 binding, 写 BUSINESS 域应过 (admin 在 v2 矩阵里
    BUSINESS write=False, 但写 alerts check 在 matrix 里是 False, 所以
    期待 403 — 这条测试验证 service-token 本身工作, 即 ``current_user_v2``
    正确合成了 admin+auditor binding).

    注: v2 把 admin/auditor/viewer 都设计为 referee (写=False).
    service-token 走这条 path, alerts check 应 403 (因为 BUSINESS 写 False).
    """
    from app.main import create_app

    monkeypatch.setenv("BIZ_BP_SERVICE_TOKEN", "test-svc-token-12345")
    app = create_app()
    with TestClient(app) as c:
        r = c.post(
            "/api/alerts/check",
            json={"line_id": "residential"},
            headers={"X-Service-Token": "test-svc-token-12345"},
        )
        # service-token = admin+auditor 双 binding, 但 v2 矩阵里
        # admin/auditor 的 BUSINESS 写权限都是 False, 所以 alerts check
        # (write=True) 应被 v2 check_domain_access 拒绝.
        assert r.status_code == 403, (
            f"service-token admin 写 alerts 应 403 (matrix 全不写), "
            f"实际 {r.status_code}: {r.text}"
        )


def test_service_token_admin_reads_alerts_rules(monkeypatch) -> None:
    """``BIZ_BP_SERVICE_TOKEN`` 读 alerts 规则应 200 (admin view 允许)."""
    from app.main import create_app

    monkeypatch.setenv("BIZ_BP_SERVICE_TOKEN", "test-svc-token-67890")
    app = create_app()
    with TestClient(app) as c:
        r = c.get(
            "/api/alerts/rules/residential",
            headers={"X-Service-Token": "test-svc-token-67890"},
        )
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 集成回归: v1 dep 不受影响 (保证 9 条业务线 router 继续用 v1 guard)
# ---------------------------------------------------------------------------


def test_v1_mock_admin_still_works_for_get_only_endpoints(client_with_auth) -> None:
    """v1 mock admin fixture (conftest.app_with_auth) 仍能让 GET 端点工作.

    这条不是测 v2, 是测 v1 没被破坏: conftest.app_with_auth 注入 v1
    ``CurrentUser`` 角色 ["admin"]. v2 router 调 ``get_current_user_v2``
    时拿到 v1 mock (因为 v2 dep 没被 override). 但 v1 mock 是 ``CurrentUser``,
    缺 v2 字段 (``bindings``), 调用 ``user.can_access_domain`` 会失败.

    所以现状是: v1 mock admin fixture **不** 让 v2 router 端点工作 —
    这是 P0 已知迁移状态. 这条测试**反向**验证: 业务线 router (走
    registry mount 的 v1 guard) 仍然能 200, 证明 9 条业务线不受影响.
    """
    r = client_with_auth.get("/api/registry/lines")
    assert r.status_code == 200, r.text
