"""
apps/api/tests/test_rbac_v2.py

Pytest 覆盖 RBAC v2 (InsightBP) 8 角色 × 5 数据域 × 读/写矩阵
================================================================

背景
----
RBAC v2 在 commit 2012244 合入 `app/core/rbac_v2.py` + `app/core/auth_v2.py`,
定义 8 角色 × 5 数据域 × 读/写 静态权限矩阵 (PERMISSION_MATRIX),并
约束:
    • FIN 视角与 HR 视角物理隔离 (fin_bp 看不到 hr, hr_bp 看不到 finance)
    • 跨业务线访问必须 *_global 角色
    • 业务线范围角色 (line_owner / fin_bp / hr_bp) 必须绑 line_id

设计选择
--------
1. **单 binding 用例**: `can_access_domain()` 是 **union-of-permissions**
   设计 (多 binding 取并集).  本测试用 `make_user()` 给每个用例构造
   **单 binding** `CurrentUserV2`,避免 union 干扰,独立测每个角色的纯行为.

2. **line_id 约定**:
   - global scope 角色 (admin/auditor/viewer/fin_bp_global/hr_bp_global)
     → `line_id=None` (放在 `business_line_id` 字段,`accessible_lines=[]`)
   - business_line scope 角色 (line_owner/fin_bp/hr_bp)
     → `line_id="residential"` (占位)
   - 跨业务线测试中,新 line_id 用 `"retail"` / `"valuation"` / `"advisory"`
     / `"project-management"` (都是已注册的业务线)

3. **覆盖率**: ~100 test cases
   - Test 1:  80 = 8 角色 × 5 域 × 2 (读/写)  — parametrize
   - Test 2-9: 行为约束 / FastAPI dep / copilot 提示词 / 优雅降级
   - Test 10: _v1_role_to_bindings 启发式 (load_user_v2 在 v1 schema 下的 fallback)

执行
----
    cd apps/api
    python -m pytest tests/test_rbac_v2.py -v --tb=short

参考
----
    apps/api/app/core/rbac_v2.py     角色/域/范围枚举 + 矩阵 + CurrentUserV2
    apps/api/app/core/auth_v2.py     load_user_v2 + get_current_user_v2
                                     + copilot_view_prompt_suffix
    infra/migrations/001_rbac_v2.sql  user_roles 表加 scope + line_id 列
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.rbac_v2 import (
    CurrentUserV2,
    DataDomain,
    PERMISSION_MATRIX,
    Role,
    Scope,
    UserRoleBinding,
    require_domain_access,
    require_role_v2,
)


# ---------------------------------------------------------------------------
# 工厂: 构造单 binding CurrentUserV2 (避免 union-of-permissions 干扰)
# ---------------------------------------------------------------------------


# 这些角色按设计就是 GLOBAL scope, business_line_id 必须是 None
_GLOBAL_SCOPE_ROLES: frozenset[Role] = frozenset(
    {
        Role.ADMIN,
        Role.AUDITOR,
        Role.VIEWER,
        Role.FIN_BP_GLOBAL,
        Role.HR_BP_GLOBAL,
    }
)


def make_user(role: Role, line_id: str = "residential") -> CurrentUserV2:
    """构造**单 binding** ``CurrentUserV2``(避免 union 干扰,独立测每个角色).

    Args:
        role: 8 角色之一.
        line_id: 业务线 id. 对 global 角色被忽略(强制为 None);
            对 business_line 角色必填,默认 ``"residential"``.

    Returns:
        一个含 1 个 binding 的 ``CurrentUserV2`` 实例.
    """
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
# Test 1: 8 角色 × 5 域 × 读/写 = 80 用例 (parametrize)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("domain", list(DataDomain))
@pytest.mark.parametrize("write", [False, True])
def test_can_access_domain_matches_matrix(role: Role, domain: DataDomain, write: bool) -> None:
    """单 binding 用户的 ``can_access_domain`` 必须严格等于 ``PERMISSION_MATRIX[role][domain]``.

    这是 80 用例的主覆盖,失败 = matrix 与实现不一致,或单 binding 用户的
    union 逻辑错误.
    """
    user = make_user(role)
    expected = PERMISSION_MATRIX[role][domain]["write" if write else "view"]
    actual = user.can_access_domain("residential", domain, write=write)
    assert actual == expected, (
        f"{role.value} on {domain.value} write={write}: "
        f"matrix says {expected}, got {actual}"
    )


# ---------------------------------------------------------------------------
# Test 2: 隔离铁律 (核心 v2 约束)
# ---------------------------------------------------------------------------


def test_fin_bp_cannot_view_or_write_hr() -> None:
    """FIN 视角与 HR 视角物理隔离: ``fin_bp`` 不能看 ``hr`` 域(读/写都 False)."""
    u = make_user(Role.FIN_BP)
    assert u.can_access_domain("residential", DataDomain.HR, write=False) is False
    assert u.can_access_domain("residential", DataDomain.HR, write=True) is False


def test_fin_bp_global_cannot_view_or_write_hr() -> None:
    """``fin_bp_global`` 也看不到 ``hr`` 域(同铁律,跨线也隔离)."""
    u = make_user(Role.FIN_BP_GLOBAL)
    for line in ("residential", "retail", "valuation"):
        assert u.can_access_domain(line, DataDomain.HR, write=False) is False
        assert u.can_access_domain(line, DataDomain.HR, write=True) is False


def test_hr_bp_cannot_view_or_write_finance() -> None:
    """HR 视角与 FIN 视角物理隔离: ``hr_bp`` 不能看 ``finance`` 域(读/写都 False)."""
    u = make_user(Role.HR_BP)
    assert u.can_access_domain("residential", DataDomain.FINANCE, write=False) is False
    assert u.can_access_domain("residential", DataDomain.FINANCE, write=True) is False


def test_hr_bp_global_cannot_view_or_write_finance() -> None:
    """``hr_bp_global`` 也看不到 ``finance`` 域(同铁律)."""
    u = make_user(Role.HR_BP_GLOBAL)
    for line in ("residential", "retail", "valuation"):
        assert u.can_access_domain(line, DataDomain.FINANCE, write=False) is False
        assert u.can_access_domain(line, DataDomain.FINANCE, write=True) is False


# ---------------------------------------------------------------------------
# Test 3: 跨业务线必须 *_global
# ---------------------------------------------------------------------------


def test_fin_bp_cannot_cross_line() -> None:
    """``fin_bp`` (业务线范围) 只能访问本业务线.

    本线读/写 finance 都 True; 跨线读/写 finance 都 False.
    """
    u = make_user(Role.FIN_BP, line_id="residential")
    assert u.can_access_domain("residential", DataDomain.FINANCE, write=False) is True
    assert u.can_access_domain("residential", DataDomain.FINANCE, write=True) is True
    assert u.can_access_domain("retail", DataDomain.FINANCE, write=False) is False
    assert u.can_access_domain("retail", DataDomain.FINANCE, write=True) is False


def test_fin_bp_global_can_cross_line_finance() -> None:
    """``fin_bp_global`` 跨业务线都能读写 ``finance`` (按 PERMISSION_MATRIX).

    注意: global 角色对其它域 (business/project 只读; hr/client 全 False)
    的具体行为由 Test 1 覆盖,这里只断言 finance 跨线.
    """
    u = make_user(Role.FIN_BP_GLOBAL)
    cross_lines = ["residential", "retail", "valuation", "advisory", "project-management"]
    for line in cross_lines:
        assert u.can_access_domain(line, DataDomain.FINANCE, write=False) is True, (
            f"fin_bp_global should view {line}/finance"
        )
        assert u.can_access_domain(line, DataDomain.FINANCE, write=True) is True, (
            f"fin_bp_global should write {line}/finance"
        )


def test_hr_bp_global_can_cross_line_hr() -> None:
    """``hr_bp_global`` 跨业务线都能读写 ``hr`` (按 PERMISSION_MATRIX)."""
    u = make_user(Role.HR_BP_GLOBAL)
    cross_lines = ["residential", "retail", "valuation", "advisory", "project-management"]
    for line in cross_lines:
        assert u.can_access_domain(line, DataDomain.HR, write=False) is True
        assert u.can_access_domain(line, DataDomain.HR, write=True) is True


def test_line_scoped_role_cannot_view_other_line_via_global_binding() -> None:
    """``line_owner`` 的 global scope 视角: 即使有 admin/auditor binding,
    业务线范围的数据还是受 line_id 约束. (本用例仅 line_owner 单 binding,验证
    ``bindings_for_line`` 不把 business_line binding 错配到其它 line.)
    """
    u = make_user(Role.LINE_OWNER, line_id="residential")
    # 本线全权
    assert u.can_access_domain("residential", DataDomain.FINANCE, write=True) is True
    # 跨线全部 False
    for other in ("retail", "valuation", "advisory", "project-management"):
        for d in DataDomain:
            assert u.can_access_domain(other, d, write=False) is False
            assert u.can_access_domain(other, d, write=True) is False


# ---------------------------------------------------------------------------
# Test 4: admin / auditor / viewer 全 view, 全不 write (referee-player)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.ADMIN, Role.AUDITOR, Role.VIEWER])
def test_admin_auditor_viewer_readonly(role: Role) -> None:
    """``admin/auditor/viewer`` 对所有域可读, 不可写 (避免裁判运动员).

    这三个角色都是 GLOBAL scope, 业务上被设计成"裁判"——可看所有数据但不写,
    业务数据写入由 line_owner / fin_bp / hr_bp 完成.
    """
    u = make_user(role)
    for domain in DataDomain:
        assert u.can_access_domain("residential", domain, write=False) is True, (
            f"{role.value} should view {domain.value}"
        )
        assert u.can_access_domain("residential", domain, write=True) is False, (
            f"{role.value} should NOT write {domain.value}"
        )


# ---------------------------------------------------------------------------
# Test 5: line_owner 本线全权, 跨线不可
# ---------------------------------------------------------------------------


def test_line_owner_full_access_on_own_line() -> None:
    """``line_owner`` 在本业务线对所有 5 域读/写都 True."""
    u = make_user(Role.LINE_OWNER, line_id="residential")
    for domain in DataDomain:
        assert u.can_access_domain("residential", domain, write=False) is True
        assert u.can_access_domain("residential", domain, write=True) is True


def test_line_owner_cannot_access_other_line() -> None:
    """``line_owner`` 不能跨业务线 (任何域任何读写都 False)."""
    u = make_user(Role.LINE_OWNER, line_id="residential")
    for other in ("retail", "valuation", "advisory"):
        for d in DataDomain:
            assert u.can_access_domain(other, d, write=False) is False
            assert u.can_access_domain(other, d, write=True) is False


# ---------------------------------------------------------------------------
# Test 6: 辅助方法 (has_role / has_admin / has_auditor / has_global_scope /
#         active_perspective)
# ---------------------------------------------------------------------------


def test_has_role_helpers_for_fin_bp() -> None:
    """``fin_bp`` 角色自身的 has_role 助手行为."""
    u = make_user(Role.FIN_BP)
    assert u.has_role(Role.FIN_BP) is True
    assert u.has_role(Role.HR_BP) is False
    assert u.has_role("fin_bp") is True
    assert u.has_role("hr_bp") is False
    assert u.has_admin() is False
    assert u.has_auditor() is False
    assert u.has_global_scope() is False
    # has_role() 无参 = True (任意角色)
    assert u.has_role() is True


def test_has_admin_helper() -> None:
    """``has_admin()`` 只对 ``admin`` 返回 True, 包括 ADMIN role string."""
    u = make_user(Role.ADMIN)
    assert u.has_admin() is True
    assert u.has_auditor() is False
    # admin 也是 global scope
    assert u.has_global_scope() is True


def test_has_auditor_helper() -> None:
    u = make_user(Role.AUDITOR)
    assert u.has_auditor() is True
    assert u.has_admin() is False
    assert u.has_global_scope() is True


def test_has_global_scope_for_fin_bp_global() -> None:
    u = make_user(Role.FIN_BP_GLOBAL)
    assert u.has_global_scope() is True
    # fin_bp_global 仍有 fin 视角
    assert u.active_perspective() == "fin"


def test_has_global_scope_false_for_line_scoped_roles() -> None:
    """``line_owner/fin_bp/hr_bp`` 都是业务线范围, ``has_global_scope`` = False."""
    for role in (Role.LINE_OWNER, Role.FIN_BP, Role.HR_BP):
        u = make_user(role, line_id="residential")
        assert u.has_global_scope() is False, f"{role.value} should not have global scope"


@pytest.mark.parametrize(
    "role,expected_view",
    [
        (Role.ADMIN, "admin"),
        (Role.AUDITOR, "auditor"),
        (Role.VIEWER, "viewer"),
        (Role.LINE_OWNER, "line_owner"),
        (Role.FIN_BP, "fin"),
        (Role.HR_BP, "hr"),
        (Role.FIN_BP_GLOBAL, "fin"),
        (Role.HR_BP_GLOBAL, "hr"),
    ],
)
def test_active_perspective_priority(role: Role, expected_view: str) -> None:
    """``active_perspective()`` 单 binding 用户按角色返回正确视角.

    优先级 (高 → 低): admin > fin/fin_global > hr/hr_global > line_owner >
    auditor > viewer > none.
    """
    assert make_user(role).active_perspective() == expected_view


# ---------------------------------------------------------------------------
# Test 7: bindings_for_line / filter_accessible_lines / can_view_line /
#         can_write_line
# ---------------------------------------------------------------------------


def test_bindings_for_line_returns_global_plus_matching_line() -> None:
    """``bindings_for_line`` 必须: 本线 binding + 所有 global binding."""
    u = CurrentUserV2(
        id=1,
        username="x",
        display_name="X",
        email=None,
        is_active=True,
        roles=["fin_bp", "fin_bp_global"],
        accessible_lines=["residential"],
        bindings=[
            UserRoleBinding(
                role=Role.FIN_BP,
                scope=Scope.BUSINESS_LINE,
                business_line_id="residential",
            ),
            UserRoleBinding(
                role=Role.FIN_BP_GLOBAL,
                scope=Scope.GLOBAL,
                business_line_id=None,
            ),
        ],
    )
    # 本线: 本线 binding + global binding
    assert len(u.bindings_for_line("residential")) == 2
    # 跨线: 只 global binding
    assert len(u.bindings_for_line("retail")) == 1
    assert u.bindings_for_line("retail")[0].role == Role.FIN_BP_GLOBAL


def test_bindings_for_line_empty_for_no_match() -> None:
    """业务线角色没有匹配的 line, 也没有 global binding → 空列表."""
    u = make_user(Role.LINE_OWNER, line_id="residential")
    assert u.bindings_for_line("residential") != []
    assert u.bindings_for_line("retail") == []


def test_filter_accessible_lines_for_line_scoped_role() -> None:
    """``fin_bp`` 只看到自己 line (其它 line 被过滤)."""
    u = make_user(Role.FIN_BP, line_id="residential")
    all_lines = ["residential", "retail", "valuation"]
    assert u.filter_accessible_lines(all_lines) == ["residential"]


def test_filter_accessible_lines_for_global_role() -> None:
    """``fin_bp_global`` (global scope) 看到所有 line, 不受 accessible_lines 限制."""
    u = make_user(Role.FIN_BP_GLOBAL)
    all_lines = ["residential", "retail", "valuation", "advisory", "project-management"]
    assert u.filter_accessible_lines(all_lines) == all_lines


def test_filter_accessible_lines_preserves_input_order() -> None:
    """``filter_accessible_lines`` 保留输入顺序 (list, not set)."""
    u = make_user(Role.FIN_BP_GLOBAL)
    shuffled = ["valuation", "advisory", "residential", "retail"]
    assert u.filter_accessible_lines(shuffled) == shuffled


def test_can_view_line_admin() -> None:
    """admin (GLOBAL) 可看任何 line, 但不能写 (referee-player)."""
    u = make_user(Role.ADMIN)
    assert u.can_view_line("residential") is True
    assert u.can_view_line("retail") is True
    assert u.can_write_line("residential") is False
    assert u.can_write_line("retail") is False


def test_can_view_line_auditor() -> None:
    """auditor (GLOBAL) 可看任何 line, 不可写 (matrix 全域 write=False).

    P1 修复: 旧实现对 global+non-admin 一律返 True, 把 auditor/viewer
    也错算成"能写". 修复后 ``can_write_line`` 拒绝 admin/auditor/viewer
    三个全局只读角色.
    """
    u = make_user(Role.AUDITOR)
    assert u.can_view_line("residential") is True
    assert u.can_view_line("retail") is True
    assert u.can_write_line("residential") is False
    assert u.can_write_line("retail") is False


def test_can_view_line_viewer() -> None:
    """viewer (GLOBAL) 跟 auditor 行为一致: 可看不可写."""
    u = make_user(Role.VIEWER)
    assert u.can_view_line("residential") is True
    assert u.can_view_line("retail") is True
    assert u.can_write_line("residential") is False
    assert u.can_write_line("retail") is False


def test_can_write_line_global_readonly_roles_rejected() -> None:
    """参数化覆盖: admin / auditor / viewer 全部 ``can_write_line=False``.

    对照 fin_bp_global / hr_bp_global 必须 ``can_write_line=True``
    (它们按各自域权限写, 由 ``can_access_domain`` 决定具体域).
    """
    for role in (Role.ADMIN, Role.AUDITOR, Role.VIEWER):
        u = make_user(role)
        assert u.can_write_line("residential") is False, (
            f"{role.value} 应该拒绝写"
        )
    for role in (Role.FIN_BP_GLOBAL, Role.HR_BP_GLOBAL):
        u = make_user(role)
        assert u.can_write_line("residential") is True, (
            f"{role.value} 应该允许写 (具体域由 can_access_domain 决定)"
        )


def test_can_view_line_and_write_line_owner() -> None:
    """``line_owner`` 本线可看可写; 跨线都不可."""
    u = make_user(Role.LINE_OWNER, line_id="residential")
    assert u.can_view_line("residential") is True
    assert u.can_write_line("residential") is True
    assert u.can_view_line("retail") is False
    assert u.can_write_line("retail") is False


def test_can_view_line_and_write_fin_bp() -> None:
    """``fin_bp`` 本线可看可写 (matrix 允许的域); 跨线都不可."""
    u = make_user(Role.FIN_BP, line_id="residential")
    assert u.can_view_line("residential") is True
    assert u.can_view_line("retail") is False
    assert u.can_write_line("residential") is True
    assert u.can_write_line("retail") is False


def test_can_write_line_for_fin_bp_global() -> None:
    """``fin_bp_global`` (GLOBAL non-admin) ``can_write_line`` 返回 True
    (由 matrix 决定具体哪个域可写)."""
    u = make_user(Role.FIN_BP_GLOBAL)
    for line in ("residential", "retail", "valuation"):
        assert u.can_write_line(line) is True


# ---------------------------------------------------------------------------
# Test 8: require_role_v2 / require_domain_access (FastAPI dep 烟雾测试)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_role_v2_accepts_matching_role() -> None:
    """``require_role_v2(allowed)`` 给出匹配角色时直接 return user."""
    u = make_user(Role.ADMIN)
    dep = require_role_v2(Role.ADMIN, Role.AUDITOR)
    result = await dep(user=u)
    assert result is u


@pytest.mark.asyncio
async def test_require_role_v2_rejects_missing_role() -> None:
    """``require_role_v2(ADMIN)`` 对 VIEWER 用户抛 403 HTTPException."""
    u = make_user(Role.VIEWER)
    dep = require_role_v2(Role.ADMIN)
    with pytest.raises(HTTPException) as exc_info:
        await dep(user=u)
    assert exc_info.value.status_code == 403
    # detail 应包含 user 实际 roles
    assert "viewer" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_require_role_v2_with_no_allowed_roles_passes() -> None:
    """``require_role_v2()`` 无参 = 不限制, 任何 user 都过."""
    u = make_user(Role.VIEWER)
    dep = require_role_v2()
    result = await dep(user=u)
    assert result is u


@pytest.mark.asyncio
async def test_require_role_v2_accepts_string_role() -> None:
    """``require_role_v2("admin")`` 也支持字符串 (非 Role enum)."""
    u = make_user(Role.ADMIN)
    dep = require_role_v2("admin")
    result = await dep(user=u)
    assert result is u


class _FakeRequest:
    """Minimal Request stub: only needs ``path_params`` dict for ``require_domain_access``.

    Starlette's ``Request.path_params`` is a read-only @property, so we
    can't easily inject it on a real ``Request``. The dep only calls
    ``request.path_params.get(line_id_param)`` so a tiny stub is enough.
    """
    def __init__(self, path_params: dict[str, str]) -> None:
        self.path_params = path_params


@pytest.mark.asyncio
async def test_require_domain_access_returns_user_when_allowed() -> None:
    """``require_domain_access`` 依赖函数直接调用, path_params 含 line_id,
    user.can_access_domain 允许 → return user."""
    u = make_user(Role.FIN_BP, line_id="residential")
    request = _FakeRequest({"line_id": "residential"})

    dep = require_domain_access(DataDomain.FINANCE, write=True)
    result = await dep(request=request, user=u)
    assert result is u


@pytest.mark.asyncio
async def test_require_domain_access_raises_403_when_denied() -> None:
    """``require_domain_access`` 当用户无权时抛 403."""
    # hr_bp 不能写 finance
    u = make_user(Role.HR_BP, line_id="residential")
    request = _FakeRequest({"line_id": "residential"})

    dep = require_domain_access(DataDomain.FINANCE, write=True)
    with pytest.raises(HTTPException) as exc_info:
        await dep(request=request, user=u)
    assert exc_info.value.status_code == 403
    assert "finance" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_require_domain_access_raises_400_when_no_line_id() -> None:
    """``require_domain_access`` path_params 缺 line_id 抛 400."""
    u = make_user(Role.ADMIN)
    request = _FakeRequest({})  # no line_id

    dep = require_domain_access(DataDomain.FINANCE)
    with pytest.raises(HTTPException) as exc_info:
        await dep(request=request, user=u)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Test 9: copilot_view_prompt_suffix (auth_v2)
# ---------------------------------------------------------------------------


def test_copilot_view_prompt_suffix_fin() -> None:
    """``fin`` 视角的 prompt suffix 必须含 FIN 约束 + 提醒 HR 不可见."""
    from app.core.auth_v2 import copilot_view_prompt_suffix
    suffix = copilot_view_prompt_suffix("fin")
    assert "FIN" in suffix
    # 必须提醒不要回答 HR 域
    assert "HR" in suffix or "人力" in suffix


def test_copilot_view_prompt_suffix_hr() -> None:
    """``hr`` 视角的 prompt suffix 必须含 HR 约束 + 提醒 FIN 不可见."""
    from app.core.auth_v2 import copilot_view_prompt_suffix
    suffix = copilot_view_prompt_suffix("hr")
    assert "HR" in suffix or "人力" in suffix
    # 必须提醒不要回答 FIN 域
    assert "FIN" in suffix or "财务" in suffix


def test_copilot_view_prompt_suffix_admin_empty() -> None:
    """``admin`` 视角无约束 (空字符串)."""
    from app.core.auth_v2 import copilot_view_prompt_suffix
    assert copilot_view_prompt_suffix("admin") == ""


def test_copilot_view_prompt_suffix_invalid_returns_empty() -> None:
    """无效 view 不抛, 返回空字符串 (前端用, 容错)."""
    from app.core.auth_v2 import copilot_view_prompt_suffix
    assert copilot_view_prompt_suffix("none") == ""
    assert copilot_view_prompt_suffix("foobar") == ""
    assert copilot_view_prompt_suffix("") == ""


def test_copilot_view_prompt_suffix_line_owner() -> None:
    """``line_owner`` 视角: 跨域 OK, 但 prompt 应标明域来源."""
    from app.core.auth_v2 import copilot_view_prompt_suffix
    suffix = copilot_view_prompt_suffix("line_owner")
    # line_owner suffix 应该有内容 (标明数据域来源)
    assert len(suffix) > 0
    assert "业务线" in suffix or "数据域" in suffix or "财务" in suffix


# ---------------------------------------------------------------------------
# Test 10: 优雅降级 — _v1_role_to_bindings 启发式
# ---------------------------------------------------------------------------


def test_v1_role_to_bindings_heuristic_admin_auditor_viewer() -> None:
    """``admin/auditor/viewer`` 直接映射成 GLOBAL scope binding."""
    from app.core.auth_v2 import _v1_role_to_bindings

    for v1_role in ("admin", "auditor", "viewer"):
        bindings = _v1_role_to_bindings(v1_role, [])
        assert len(bindings) == 1
        b = bindings[0]
        assert b.scope == Scope.GLOBAL
        assert b.business_line_id is None
        assert b.role.value == v1_role


def test_v1_role_to_bindings_heuristic_bp_prefix_with_line() -> None:
    """``bp:<line>`` 映射成 line_owner (BUSINESS_LINE) 当 line 在 accessible_lines 里."""
    from app.core.auth_v2 import _v1_role_to_bindings

    bindings = _v1_role_to_bindings("bp:residential", ["residential"])
    assert len(bindings) == 1
    b = bindings[0]
    assert b.role == Role.LINE_OWNER
    assert b.scope == Scope.BUSINESS_LINE
    assert b.business_line_id == "residential"


def test_v1_role_to_bindings_heuristic_orphan_dropped() -> None:
    """``bp:<orphan>`` 当 line 不在 accessible_lines → 启发式返回 [] (丢弃)."""
    from app.core.auth_v2 import _v1_role_to_bindings

    # 业务线不在 accessible_lines → 启发式应丢弃
    assert _v1_role_to_bindings("bp:orphan", []) == []
    assert _v1_role_to_bindings("bp:residential", []) == []
    assert _v1_role_to_bindings("bp:retail", ["residential"]) == []


def test_v1_role_to_bindings_heuristic_v2_role_returns_empty() -> None:
    """v1 schema 不可能有 ``fin_bp/hr_bp`` 等 v2 role 字符串, 启发式返回 []."""
    from app.core.auth_v2 import _v1_role_to_bindings

    for v2_only_role in ("fin_bp", "hr_bp", "fin_bp_global", "hr_bp_global"):
        assert _v1_role_to_bindings(v2_only_role, []) == []


def test_v1_role_to_bindings_heuristic_empty_line_id() -> None:
    """``bp:`` (空 line_id) 启发式行为: ``if line_id and ...`` 短路, 跳过孤儿
    检测, 返回 ``line_owner`` with ``business_line_id=None`` (退化情况).

    这是当前实现的真实行为, 不是 bug; 业务上 v1 seed 数据不会出现 ``bp:`` 这种
    空 line_id 的 role 字符串, 但本测试固定该行为以便未来调整.
    """
    from app.core.auth_v2 import _v1_role_to_bindings

    bindings = _v1_role_to_bindings("bp:", ["residential"])
    assert len(bindings) == 1
    b = bindings[0]
    assert b.role == Role.LINE_OWNER
    assert b.scope == Scope.BUSINESS_LINE
    assert b.business_line_id is None


def test_v1_role_to_bindings_heuristic_full_simulation() -> None:
    """端到端: 模拟 v1 schema 下的 seed 用户 role 列表, 启发式推断后用
    ``can_access_domain`` 验证业务线范围 / 跨线隔离正确."""
    from app.core.auth_v2 import _v1_role_to_bindings

    # 模拟一个 residential BP 用户 (v1 schema)
    v1_roles = [("bp:residential",)]
    bindings = _v1_role_to_bindings("bp:residential", ["residential"])
    assert len(bindings) == 1

    user = CurrentUserV2(
        id=1,
        username="bp_residential",
        display_name="BP Res",
        email=None,
        is_active=True,
        roles=[b.role.value for b in bindings],
        accessible_lines=["residential"],
        bindings=bindings,
    )
    # 本线全权 (line_owner)
    assert user.can_access_domain("residential", DataDomain.FINANCE, write=True) is True
    # 跨线不可
    assert user.can_access_domain("retail", DataDomain.FINANCE, write=False) is False


# ---------------------------------------------------------------------------
# Test 11: 边界 / union-of-permissions 验证 (文档化的行为)
# ---------------------------------------------------------------------------


def test_multi_binding_user_takes_union_of_permissions() -> None:
    """**union-of-permissions** 语义: 多 binding 用户取任一 binding 允许的并集.

    这是 ``can_access_domain`` 的设计选择, 在多 binding 用户上应观察到此行为.
    本用例用 ``fin_bp`` (本线) + ``fin_bp_global`` (跨线) 验证 union 行为.
    """
    user = CurrentUserV2(
        id=1,
        username="dual",
        display_name="Dual Binding",
        email=None,
        is_active=True,
        roles=["fin_bp", "fin_bp_global"],
        accessible_lines=["residential"],
        bindings=[
            UserRoleBinding(
                role=Role.FIN_BP,
                scope=Scope.BUSINESS_LINE,
                business_line_id="residential",
            ),
            UserRoleBinding(
                role=Role.FIN_BP_GLOBAL,
                scope=Scope.GLOBAL,
                business_line_id=None,
            ),
        ],
    )
    # union 行为: 跨线可写 finance (来自 fin_bp_global)
    assert user.can_access_domain("retail", DataDomain.FINANCE, write=True) is True
    # hr 域任一 binding 都 False → union 仍 False
    assert user.can_access_domain("residential", DataDomain.HR, write=False) is False
    assert user.can_access_domain("retail", DataDomain.HR, write=False) is False


def test_user_with_no_bindings_denies_everything() -> None:
    """无 binding 用户 → 所有 ``can_access_domain`` 调用都 False."""
    u = CurrentUserV2(
        id=1,
        username="nobindings",
        display_name="No Bindings",
        email=None,
        is_active=True,
        roles=[],
        accessible_lines=[],
        bindings=[],
    )
    for line in ("residential", "retail", "valuation"):
        for d in DataDomain:
            assert u.can_access_domain(line, d, write=False) is False
            assert u.can_access_domain(line, d, write=True) is False
    # 无 binding 的 active_perspective 应该是 "none"
    assert u.active_perspective() == "none"


def test_user_with_line_but_no_matching_binding_denies() -> None:
    """``accessible_lines`` 里有 line 但无 binding → ``can_access_domain`` 仍 False
    (binding 才是 source of truth, accessible_lines 只用于 ``filter_accessible_lines``).
    """
    u = make_user(Role.LINE_OWNER, line_id="residential")
    # 本线有 binding → True
    assert u.can_access_domain("residential", DataDomain.BUSINESS, write=False) is True
    # 跨线无 binding → False (即使 accessible_lines 里有也无关)
    assert u.can_access_domain("valuation", DataDomain.BUSINESS, write=False) is False


# ---------------------------------------------------------------------------
# Test 12: has_role 复合查询
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,allowed_roles,expected",
    [
        (Role.ADMIN, [Role.ADMIN, Role.AUDITOR], True),
        (Role.ADMIN, [Role.AUDITOR, Role.VIEWER], False),
        (Role.FIN_BP, [Role.FIN_BP, Role.HR_BP], True),
        (Role.FIN_BP, [Role.HR_BP], False),
        (Role.LINE_OWNER, [], True),  # 无参 = True
        (Role.VIEWER, [Role.VIEWER], True),
    ],
)
def test_has_role_composite(role: Role, allowed_roles: list, expected: bool) -> None:
    u = make_user(role)
    assert u.has_role(*allowed_roles) is expected
