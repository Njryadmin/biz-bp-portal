"""
apps/api/app/core/rbac_v2.py

RBAC v2 — InsightBP / 业务洞察平台 8 角色模型。

角色 (源自世界5大房地产咨询公司组织结构,经与甲方确认):

    1. admin            ── 集团 IT / 平台运营
    2. auditor          ── 集团内审 / 合规
    3. viewer           ── 集团高管 (CEO/COO) 只读
    4. line_owner       ── 业务线总监/合伙人
    5. fin_bp           ── 业务线 FINBP  (业务线范围)
    6. hr_bp            ── 业务线 HRBP   (业务线范围)
    7. fin_bp_global    ── 集团 FINBP
    8. hr_bp_global     ── 集团 HRBP

核心约束:
    • FIN 视角与 HR 视角物理隔离
    • 跨业务线访问需要 *_global 角色
    • 业务线范围的角色必须绑 line_id (通过 user_business_lines)
    • 所有权限判断基于 (role_id, scope, line_id, data_domain) 四元组
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from fastapi import HTTPException, status


# ---------------------------------------------------------------------------
# 角色 / 数据域 / 范围 枚举
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """8 角色固定枚举。新增角色必须显式加这里 + DB seed 数据。"""
    ADMIN = "admin"
    AUDITOR = "auditor"
    VIEWER = "viewer"
    LINE_OWNER = "line_owner"
    FIN_BP = "fin_bp"
    HR_BP = "hr_bp"
    FIN_BP_GLOBAL = "fin_bp_global"
    HR_BP_GLOBAL = "hr_bp_global"


class DataDomain(str, Enum):
    """5 个数据域。manifest.yaml.data_scope.domains 列在这里面。"""
    BUSINESS = "business"   # 业务指标 (IRR / 坪效 / 项目数)
    FINANCE = "finance"     # 财务数据 (P&L / 预算 / 应收账款)
    HR = "hr"               # 人力数据 (薪资 / 招聘 / 绩效)
    CLIENT = "client"       # 客户数据 (客户名 / 合同 / 续约)
    PROJECT = "project"     # 项目数据 (项目编号 / 进度 / 团队)


class Scope(str, Enum):
    """角色作用域。"""
    GLOBAL = "global"
    BUSINESS_LINE = "business_line"


# ---------------------------------------------------------------------------
# 权限矩阵 — 静态配置,启动期加载
# ---------------------------------------------------------------------------

# role_id → {domain: {view: bool, write: bool}}
# view=True 表示可读, write=True 表示可写
PERMISSION_MATRIX: dict[Role, dict[DataDomain, dict[str, bool]]] = {
    # admin: 全权限,但禁写业务数据(避免裁判运动员)
    # 业务数据写入由 line_owner / fin_bp / hr_bp 完成
    Role.ADMIN: {
        DataDomain.BUSINESS: {"view": True, "write": False},
        DataDomain.FINANCE:  {"view": True, "write": False},
        DataDomain.HR:       {"view": True, "write": False},
        DataDomain.CLIENT:   {"view": True, "write": False},
        DataDomain.PROJECT:  {"view": True, "write": False},
    },
    # auditor: 只读全部,审计日志特殊权限在外
    Role.AUDITOR: {
        DataDomain.BUSINESS: {"view": True, "write": False},
        DataDomain.FINANCE:  {"view": True, "write": False},
        DataDomain.HR:       {"view": True, "write": False},
        DataDomain.CLIENT:   {"view": True, "write": False},
        DataDomain.PROJECT:  {"view": True, "write": False},
    },
    # viewer: 全只读
    Role.VIEWER: {
        DataDomain.BUSINESS: {"view": True, "write": False},
        DataDomain.FINANCE:  {"view": True, "write": False},
        DataDomain.HR:       {"view": True, "write": False},
        DataDomain.CLIENT:   {"view": True, "write": False},
        DataDomain.PROJECT:  {"view": True, "write": False},
    },
    # line_owner: 本业务线全权限(无域限制)
    Role.LINE_OWNER: {
        DataDomain.BUSINESS: {"view": True, "write": True},
        DataDomain.FINANCE:  {"view": True, "write": True},
        DataDomain.HR:       {"view": True, "write": True},
        DataDomain.CLIENT:   {"view": True, "write": True},
        DataDomain.PROJECT:  {"view": True, "write": True},
    },
    # fin_bp(line): 本业务线 business/finance/project 可读写,hr/client 只读
    # 注意: fin_bp 看不到 hr 域(铁律)
    Role.FIN_BP: {
        DataDomain.BUSINESS: {"view": True, "write": True},
        DataDomain.FINANCE:  {"view": True, "write": True},
        DataDomain.HR:       {"view": False, "write": False},
        DataDomain.CLIENT:   {"view": True, "write": False},
        DataDomain.PROJECT:  {"view": True, "write": True},
    },
    # hr_bp(line): 本业务线 business/hr/client/project 可读写,finance 不可见
    Role.HR_BP: {
        DataDomain.BUSINESS: {"view": True, "write": False},
        DataDomain.FINANCE:  {"view": False, "write": False},
        DataDomain.HR:       {"view": True, "write": True},
        DataDomain.CLIENT:   {"view": True, "write": True},
        DataDomain.PROJECT:  {"view": True, "write": False},
    },
    # fin_bp_global: 跨业务线 finance 读写 + business 元数据只读
    Role.FIN_BP_GLOBAL: {
        DataDomain.BUSINESS: {"view": True, "write": False},
        DataDomain.FINANCE:  {"view": True, "write": True},
        DataDomain.HR:       {"view": False, "write": False},
        DataDomain.CLIENT:   {"view": False, "write": False},
        DataDomain.PROJECT:  {"view": True, "write": False},
    },
    # hr_bp_global: 跨业务线 hr 读写 + business 元数据只读
    Role.HR_BP_GLOBAL: {
        DataDomain.BUSINESS: {"view": True, "write": False},
        DataDomain.FINANCE:  {"view": False, "write": False},
        DataDomain.HR:       {"view": True, "write": True},
        DataDomain.CLIENT:   {"view": False, "write": False},
        DataDomain.PROJECT:  {"view": False, "write": False},
    },
}


# role → scope 映射(硬约束,不允许角色被错误赋给错误 scope)
ROLE_SCOPE: dict[Role, Scope] = {
    Role.ADMIN: Scope.GLOBAL,
    Role.AUDITOR: Scope.GLOBAL,
    Role.VIEWER: Scope.GLOBAL,
    Role.LINE_OWNER: Scope.BUSINESS_LINE,
    Role.FIN_BP: Scope.BUSINESS_LINE,
    Role.HR_BP: Scope.BUSINESS_LINE,
    Role.FIN_BP_GLOBAL: Scope.GLOBAL,
    Role.HR_BP_GLOBAL: Scope.GLOBAL,
}


# ---------------------------------------------------------------------------
# 权限判断核心
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class UserRoleBinding:
    """一个用户角色绑定记录。来自 user_roles 表。"""
    role: Role
    scope: Scope
    business_line_id: str | None  # scope=BUSINESS_LINE 时必填


@dataclass(slots=True)
class CurrentUserV2:
    """v2 版 CurrentUser,支持 FIN/HR 双视角。

    与 v1 兼容点:
      • id / username / display_name / email / is_active 字段保留
      • roles 字段保留(向后兼容)
      • accessible_lines 字段保留

    新增:
      • bindings: 完整角色绑定列表(role + scope + line_id)
      • active_view: 当前激活的视角 ("fin" / "hr" / "line_owner" / "admin" ...)
    """
    id: int
    username: str
    display_name: str
    email: str | None
    is_active: bool
    roles: list[str]
    accessible_lines: list[str]
    bindings: list[UserRoleBinding]
    active_view: str | None = None  # 视角切换: "fin" / "hr" / None (= 按请求路由自动)

    def has_role(self, *roles: str | Role) -> bool:
        if not roles:
            return True
        wanted = {r.value if isinstance(r, Role) else r for r in roles}
        return any(r in self.roles for r in wanted)

    def has_admin(self) -> bool:
        return self.has_role(Role.ADMIN)

    def has_auditor(self) -> bool:
        return self.has_role(Role.AUDITOR)

    def has_global_scope(self) -> bool:
        """是否拥有任何 global scope 角色。"""
        return any(b.scope == Scope.GLOBAL for b in self.bindings)

    def bindings_for_line(self, line_id: str) -> list[UserRoleBinding]:
        """返回用户在该业务线上的所有绑定(包含 global scope 角色)。"""
        out = []
        for b in self.bindings:
            if b.scope == Scope.GLOBAL:
                out.append(b)
            elif b.scope == Scope.BUSINESS_LINE and b.business_line_id == line_id:
                out.append(b)
        return out

    def can_view_line(self, line_id: str) -> bool:
        """是否能读这条业务线的任何域。"""
        # global 角色都可看
        for b in self.bindings:
            if b.scope == Scope.GLOBAL:
                return True
        # 业务线范围内有绑定
        return any(
            b.scope == Scope.BUSINESS_LINE and b.business_line_id == line_id
            for b in self.bindings
        )

    def can_write_line(self, line_id: str) -> bool:
        """是否能写这条业务线。admin 不行(避免裁判运动员);其他都行。"""
        for b in self.bindings:
            if b.scope == Scope.GLOBAL and b.role != Role.ADMIN:
                return True  # fin_bp_global / hr_bp_global 可写(各自域)
            if b.scope == Scope.BUSINESS_LINE and b.business_line_id == line_id:
                # fin_bp / hr_bp / line_owner 都可写(各自允许的域)
                if b.role in (Role.LINE_OWNER, Role.FIN_BP, Role.HR_BP):
                    return True
        return False

    def can_access_domain(
        self,
        line_id: str,
        domain: DataDomain,
        write: bool = False,
    ) -> bool:
        """核心判断:用户能否访问某业务线的某数据域。

        Args:
            line_id: 业务线 ID
            domain: 数据域
            write: True=写, False=读

        Returns:
            True=允许, False=拒绝

        Raises:
            无显式异常;调用方根据 bool 决定是否抛 403
        """
        bindings = self.bindings_for_line(line_id)
        if not bindings:
            return False

        for b in bindings:
            matrix = PERMISSION_MATRIX[b.role]
            perm = matrix.get(domain, {"view": False, "write": False})
            if write and not perm["write"]:
                continue
            if not write and not perm["view"]:
                continue
            return True

        return False

    def filter_accessible_lines(
        self, all_line_ids: Iterable[str]
    ) -> list[str]:
        """返回用户能看到的业务线列表。"""
        if self.has_global_scope():
            return list(all_line_ids)
        allowed = set(self.accessible_lines or [])
        return [lid for lid in all_line_ids if lid in allowed]

    def active_perspective(self) -> str:
        """返回当前激活的业务视角:fin / hr / line_owner / admin。

        用于 UI 切换 + Copilot system_prompt 选模板。
        """
        if self.active_view:
            return self.active_view
        # 自动按"最强"角色判定
        if self.has_admin():
            return "admin"
        if self.has_role(Role.FIN_BP_GLOBAL) or self.has_role(Role.FIN_BP):
            return "fin"
        if self.has_role(Role.HR_BP_GLOBAL) or self.has_role(Role.HR_BP):
            return "hr"
        if self.has_role(Role.LINE_OWNER):
            return "line_owner"
        if self.has_auditor():
            return "auditor"
        if self.has_role(Role.VIEWER):
            return "viewer"
        return "none"


# ---------------------------------------------------------------------------
# FastAPI Dependencies
# ---------------------------------------------------------------------------


def require_role_v2(*allowed_roles: Role | str):
    """FastAPI dependency: 用户必须拥有任一允许的角色。"""
    wanted = tuple(r.value if isinstance(r, Role) else r for r in allowed_roles)

    async def _dep(user: CurrentUserV2) -> CurrentUserV2:
        if not wanted:
            return user
        if user.has_role(*wanted):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role required: one of {list(wanted)}; user has {user.roles}",
        )

    return _dep


def require_domain_access(
    domain: DataDomain,
    line_id_param: str = "line_id",
    write: bool = False,
):
    """FastAPI dependency: 用户对 (line_id, domain) 必须有访问权。

    用法:
        @router.get("/lines/{line_id}/finance/summary",
                    dependencies=[Depends(require_domain_access(DataDomain.FINANCE))])
    """
    async def _dep(request, user: CurrentUserV2) -> CurrentUserV2:
        line_id = request.path_params.get(line_id_param)
        if not line_id:
            raise HTTPException(400, "line_id is required")
        if not user.can_access_domain(line_id, domain, write=write):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"no {'write' if write else 'view'} access to domain '{domain.value}' "
                    f"on business line '{line_id}'; "
                    f"user roles={user.roles}"
                ),
            )
        return user

    return _dep


__all__ = [
    "Role",
    "DataDomain",
    "Scope",
    "UserRoleBinding",
    "CurrentUserV2",
    "PERMISSION_MATRIX",
    "ROLE_SCOPE",
    "require_role_v2",
    "require_domain_access",
]