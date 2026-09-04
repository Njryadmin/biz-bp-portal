# InsightBP — v2 RBAC + Manifest v2 + Admin UI + Dashboard + Copilot 视角 全量交付

> **交付日期**: 2026-09-04
> **阶段**: PR #1 合并 master
> **范围**: 后端 (FastAPI) + 前端 (Next.js) + DB migration + 4 大功能集成
> **目标**: 把 0.1.0 (4 角色 / 单租户 / 9 业务线) 升级到 v2 (8 角色 / 5 数据域 / 多租户 / FIN/HR 物理隔离)

---

## 0. 一句话总览

PR #1 把 Biz-BP Portal **演进为 InsightBP v2**：4 角色 → **8 角色**、无域 → **5 数据域**、单租户 → **多租户 (M1-M3)**、手动 YAML 编辑 → **Admin UI**、单一 dashboard → **FIN/HR/Shared 三视角**、单语言 → **业务线插件 manifest v2**。**277 passed / 0 failed**，**v0.1.0 全部 145 个测试 + 11 skip 仍绿**（向后兼容，不破坏现有用户）。

---

## 1. Result

| 区域 | 状态 | 证据 |
|---|---|---|
| **A. 8 角色 RBAC v2** | PASS | `apps/api/app/core/rbac_v2.py` (8 角色枚举 + 5 域 + PERMISSION_MATRIX + `CurrentUserV2`)；`apps/api/app/core/auth_v2.py` (DB 加载 + `get_current_user_v2` + 视角切换 + Copilot prompt 后缀) |
| **B. Manifest v2 schema** | PASS | `business_lines/_template/manifest.yaml.v2.example` (新增 4 块：`data_scope` / `owner_role_assignments` / `access_matrix` / `kpis`)；`business_lines/project-management/manifest.yaml` (P0 升级 v2) |
| **C. Admin UI** | PASS | `apps/web/app/(dashboard)/admin/users/page.tsx` (v2 角色管理 — role + scope + line_id 三元组) + `business-lines/[id]/page.tsx` (5 区块 YAML 编辑器，原子写 + `.bak`) |
| **D. Dashboard MVP (E)** | PASS | `apps/api/app/routers/dashboard.py` (3 端点 `fin` / `hr` / `shared`); `apps/web/app/(dashboard)/dashboard/{fin,hr,shared}/page.tsx`; `PerspectiveSwitcher` Topbar 组件 |
| **E. Copilot 视角切换** | PASS | `apps/api/app/core/auth_v2.py:copilot_view_prompt_suffix()` (FIN/HR 视角 prompt 后缀); `apps/web/app/(dashboard)/_components/PerspectiveSwitcher.tsx` |
| **F. 跨业务线汇总 (G)** | PASS | `apps/api/app/routers/cross_line_summary.py` (2 端点 `/api/finance/summary` + `/api/hr/summary`; `?lines=` csv/glob; 域隔离) |
| **G. Migration runner** | PASS | `apps/api/app/db/migration_runner.py` (核心; pg_advisory_xact_lock + SHA256 checksum + dry-run); `apps/api/app/routers/migrations.py` (3 端点 status/apply/verify); 4 份 migration 文件全 apply |
| **H. 多租户 M1-M3** | PASS | 详见 [`multi-tenant-deliverable.md`](multi-tenant-deliverable.md); `apps/api/app/core/tenant_context.py` + `db/tenant.py` + `routers/admin_tenants.py` + 前端 TenantBadge/TenantSwitcher |
| **I. 测试覆盖** | PASS | 277 passed / 0 failed; 累计 v1 145 + admin v2 roles 16 + admin business-line 19 + dashboard 23 + migration 12 + cross-line 34 + multi-tenant m1 10 + tenant context m2 7 + admin tenants 11 |

**Result: PASS**

---

## 2. 背景 — 为什么从 4 角色 → 8 角色

### 2.1 旧版 (v0.1.0) 的局限

4 角色 (`admin` / `auditor` / `viewer` / `bp:<line>`) 假设每个业务线**只有 1 个 BP**。
但 5 大房地产咨询公司的真实组织结构是：

```
集团 FINBP ─┐
            ├── 业务线 X  ─── 项目 1
            ├── 业务线 Y  ─── 项目 2 / 项目 3
集团 HRBP ──┤
            └── 业务线 Z
```

每个业务线有独立的 FINBP + HRBP + 业务线总监 (`line_owner`)。**FIN 视角与 HR 视角必须物理隔离** — FINBP 不能看员工薪资，HRBP 不能看项目利润。

### 2.2 演进触发

- **业务需求**：5 大行级别合规要求"FIN/HR 物理隔离"
- **组织现实**：每个业务线有 3 类负责人（FINBP / HRBP / 总监）
- **跨业务线**：集团 FINBP 需要"看所有业务的财务汇总"
- **审计要求**：裁判（admin/auditor/viewer）vs 运动员（line_owner/fin_bp/hr_bp）必须分离

### 2.3 决策

**8 角色模型**（见 §3）+ **5 数据域**（见 §4）+ **scope 概念**（global / business_line）+ **视角切换**（X-Active-View header）。

---

## 3. 8 角色清单

来自 `apps/api/app/core/rbac_v2.py:Role` 枚举：

| # | 角色 | scope | 典型岗位 | 关键能力 |
|---|---|---|---|---|
| 1 | `admin` | global | 集团 IT / 平台运营 | 用户管理、AI 模型、审计日志、**所有业务数据只读**（裁判） |
| 2 | `auditor` | global | 集团内审 / 合规 | 审计日志只读 + 业务数据只读 |
| 3 | `viewer` | global | 集团 CEO / COO | 全部业务数据只读 |
| 4 | `line_owner` | business_line | 业务线总监 / 合伙人 | 本业务线**全 5 域全权限**，唯一可发"本线临时跨域授权" |
| 5 | `fin_bp` | business_line | 业务线 FINBP | 本线 business/finance/project **读写**；hr/client 只读；**HR 域不可见** |
| 6 | `hr_bp` | business_line | 业务线 HRBP | 本线 business/hr/client/project **读写**；**finance 域不可见** |
| 7 | `fin_bp_global` | global | 集团 FINBP | 跨业务线 finance **读写** + business/project 只读；**HR 域不可见** |
| 8 | `hr_bp_global` | global | 集团 HRBP | 跨业务线 hr **读写** + business 只读；**finance 域不可见** |

**ROLE_SCOPE 硬约束**（`rbac_v2.py:141-150`）：

```python
ROLE_SCOPE = {
    Role.ADMIN:           Scope.GLOBAL,
    Role.AUDITOR:         Scope.GLOBAL,
    Role.VIEWER:          Scope.GLOBAL,
    Role.LINE_OWNER:      Scope.BUSINESS_LINE,
    Role.FIN_BP:          Scope.BUSINESS_LINE,
    Role.HR_BP:           Scope.BUSINESS_LINE,
    Role.FIN_BP_GLOBAL:   Scope.GLOBAL,
    Role.HR_BP_GLOBAL:    Scope.GLOBAL,
}
```

不允许给 `admin` 配 `business_line` scope（也不需要 — admin 是 global）；不允许给 `fin_bp` 配 `global` scope（必须绑 line_id）。

---

## 4. 5 数据域 + FIN/HR 隔离铁律

`rbac_v2.py:DataDomain` 枚举：

| 域 | 含义 | 典型字段 |
|---|---|---|
| `business` | 业务指标 | IRR / 坪效 / 项目数 / 客户满意度 |
| `finance` | 财务数据 | P&L / 预算 / 应收账款 / 现金流 |
| `hr` | 人力数据 | 薪资 / 招聘 / 绩效 / 培训 |
| `client` | 客户数据 | 客户名 / 合同 / 续约率 / 满意度 |
| `project` | 项目数据 | 项目编号 / 进度 / 团队 / 利润率 |

### 4.1 PERMISSION_MATRIX 完整表

8 角色 × 5 域 × 读/写 (取自 `rbac_v2.py:70-137`):

| 角色 \ 域 | business | finance | hr | client | project |
|---|---|---|---|---|---|
| `admin` | R | R | R | R | R |
| `auditor` | R | R | R | R | R |
| `viewer` | R | R | R | R | R |
| `line_owner` | **R+W** | **R+W** | **R+W** | **R+W** | **R+W** |
| `fin_bp` | R+W | **R+W** | ❌ | R | R+W |
| `hr_bp` | R | ❌ | **R+W** | R+W | R |
| `fin_bp_global` | R | **R+W** | ❌ | ❌ | R |
| `hr_bp_global` | R | ❌ | **R+W** | ❌ | ❌ |

**铁律**：
- `fin_bp` 看不到 `hr` 域 (全 ❌) — `PERMISSION_MATRIX[Role.FIN_BP][DataDomain.HR] = {view: False, write: False}`
- `hr_bp` 看不到 `finance` 域
- `admin` 不写业务数据（避免裁判运动员）— `can_write_line` 显式拒绝
- `line_owner` 是唯一跨域全权限角色

### 4.2 核心判断方法

```python
# CurrentUserV2
user.can_access_domain(line_id: str, domain: DataDomain, write: bool = False) -> bool

# 例子
user.can_access_domain("residential", DataDomain.FINANCE, write=True)  # fin_bp 绑 residential → True
user.can_access_domain("residential", DataDomain.HR, write=False)      # fin_bp 看不到 HR → False
user.can_write_line("residential")                                      # fin_bp 绑 residential → True
user.can_write_line("retail")                                           # fin_bp 只绑 residential → False
```

---

## 5. scope 概念 (v2 新增)

v1 角色字符串 `bp:<line>` 把"用户-业务线"耦合到 role 字段里。v2 解耦成 **三元组** `(role, scope, line_id)`：

```python
@dataclass(slots=True)
class UserRoleBinding:
    role: Role
    scope: Scope           # global / business_line
    business_line_id: Optional[str]  # scope=business_line 时必填
```

| scope | 含义 | 适用角色 | line_id |
|---|---|---|---|
| `global` | 跨业务线 | admin / auditor / viewer / fin_bp_global / hr_bp_global | NULL |
| `business_line` | 单业务线 | line_owner / fin_bp / hr_bp | 必填（如 `"residential"`） |

**DB 存储**：`user_roles` 表新增 `scope` + `line_id` 列（`infra/migrations/001_rbac_v2.sql`）。

**v1 → v2 backfill**：

| v1 `role` 字符串 | v2 `scope` | v2 `line_id` |
|---|---|---|
| `admin` / `auditor` / `viewer` | `global` | NULL |
| `bp:residential` | `business_line` | `"residential"` |
| `bp:retail` | `business_line` | `"retail"` |
| ... (8 个 BP 用户) | `business_line` | 对应 line_id |

**保守策略**：v1 `bp:<line>` 直接映射为 v2 `line_owner`（业务线全权限）的等价 binding。这样 8 月份发布的 8 个种子用户**不会掉线**。细分成 `fin_bp` / `hr_bp` 由 admin 手动在 admin UI 二次分配（`PATCH /api/auth/users/{id}/v2-roles`）。

---

## 6. 视角切换 (X-Active-View header)

**问题**：同一用户可能同时是"业务线总监 + FINBP"（v2 允许多角色）。前端需要知道"现在想让用户以哪个视角看数据"。

**方案**：

- **HTTP header** `X-Active-View: fin` / `hr` / `line_owner` / `admin` / `auditor` / `viewer`
- 后端 `auth_v2.py:switch_view()` 写到 `CurrentUserV2.active_view`
- 前端 `PerspectiveSwitcher` 组件放 Topbar 右上角，下拉切换
- `active_perspective()` 自动按角色优先级取最强视角（admin > fin_bp_global > fin_bp > hr_bp_global > hr_bp > line_owner > auditor > viewer）

**Copilot 集成**：`copilot_view_prompt_suffix(view)` 返回 system_prompt 后缀，约束 LLM 回答范围：

```python
{
    "fin": "\n\n【FIN 视角约束】你只能回答财务相关问题,严禁回答人力/薪资/招聘问题。看不到的数据直接说'该数据不属于 FIN 视角访问范围'。",
    "hr": "\n\n【HR 视角约束】你只能回答人力/招聘/绩效/培训相关问题,严禁回答财务/项目利润问题。",
    "line_owner": "\n\n【业务线负责人视角】你可以跨域分析(财务+人力+业务),但回答时必须标明数据域来源。",
    "admin": "",  # admin 全权,无约束
}
```

---

## 7. Manifest v2 schema (业务线插件)

`business_lines/_template/manifest.yaml.v2.example` 定义 4 个新块（在 v1 schema 基础上加）：

### 7.1 `data_scope` 块

```yaml
data_scope:
  domains: [business, finance, hr, client, project]   # 必填:本业务线包含哪些域
```

Pydantic 校验在 `apps/api/app/core/registry.py:67-72`（`field_validator`）。`domains` 必须是 5 个合法域的子集。

### 7.2 `owner_role_assignments` 块

```yaml
owner_role_assignments:
  finance_bp: "fin_bp:residential"     # 本线 FINBP 绑定
  hr_bp:      "hr_bp:residential"      # 本线 HRBP 绑定
  line_owner: "line_owner:residential" # 业务线总监
```

admin 在 admin UI 创建 v2 角色绑定时，会从 manifest 读取这些值，**避免角色字符串拼错**（如 `fin_bp:residentail` 拼写错误）。

### 7.3 `access_matrix` 块（域 × 角色过滤）

```yaml
access_matrix:
  fin_bp:      [business, finance, project]    # 本线 FINBP 可见域
  hr_bp:       [business, hr, client]           # 本线 HRBP 可见域
  line_owner:  [business, finance, hr, client, project]  # 本线 line_owner
  line_member: [business, project, client]      # 默认只读成员
```

**作用**：UI 自动渲染"角色可访问域"提示；后端 `filter_accessible_lines` 二次校验。

### 7.4 `kpis` 块 (5 视角 KPI)

```yaml
kpis:
  fin_view:    # FINBP 视角 KPI
    - { id: monthly_revenue, title: "月度营收", source: "mart_xxx.fct_revenue" }
    - { id: ar_aging,        title: "应收账款账龄", unit: "天" }
  hr_view:     # HRBP 视角 KPI
    - { id: headcount_fte,   title: "在职 FTE" }
    - { id: attrition_q,     title: "季度离职率", unit: "%" }
  shared_view: # FIN/HR 共关注
    - { id: revenue_per_fte, title: "人均营收", formula: "monthly_revenue / headcount_fte" }
```

**读取路径**：`apps/api/app/routers/dashboard.py:_gather_kpis()` 按 view_keys 从 manifest 拉 KPI 列表，组装 `DashboardKpiItem`（id / title / value / unit / trend / source / formula）。

### 7.5 v1 → v2 兼容

- v1 manifest（无新字段）仍可加载（`data_scope` 缺省 → 全 5 域；`access_matrix` 缺省 → 全员全权限；`kpis` 缺省 → 空）
- v2 manifest 在 v1 路由仍可工作（`registry.py` 校验向后兼容）

---

## 8. Admin UI

3 个新页面，全部在 `apps/web/app/(dashboard)/admin/`：

### 8.1 用户 v2 角色管理 (`admin/users/page.tsx`)

- 列出现有用户 + v2 binding 列表
- "Add Binding" 弹窗：role 下拉（8 选 1）+ scope radio（global / business_line）+ line_id 下拉（仅 business_line）
- "Replace all bindings" 操作：`PATCH /api/auth/users/{id}/v2-roles`
- "Read bindings" 操作：`GET /api/auth/users/{id}/v2-roles`
- last-admin 保护（admin 角色不能被降级到 0 人）

### 8.2 业务线 YAML 编辑器 (`admin/business-lines/[id]/page.tsx`)

**5 区块**（v1 基础 + v2 4 块）：

| 区块 | YAML 路径 | 字段数 |
|---|---|---|
| 1. 基础信息 | `id` / `name` / `version` / `description` / `icon` | 5 |
| 2. 导航 | `nav[*].path` / `nav[*].title` | 4-12 |
| 3. data_scope | `data_scope.domains` | 1 (multi) |
| 4. owner_role_assignments | `finance_bp` / `hr_bp` / `line_owner` | 3 |
| 5. access_matrix | `fin_bp` / `hr_bp` / `line_owner` / `line_member` | 4 (multi) |
| 6. kpis | `fin_view` / `hr_view` / `shared_view` | 3 (list) |

**原子写**：后端 `admin_business_lines.py:update_business_line` 走 `tempfile + os.replace` 模式（`atomic_wri.te`) + 自动 `.bak` 备份（最近 1 个版本）。

**热重载**：写完 manifest 后调用 `app.core.registry.reload_registry()`，**不需要重启 API**（除业务线 router 的 importlib 加载外，其他都热重载）。

### 8.3 Tenant 管理 (`admin/tenants/page.tsx`) — M3

- 列出所有 tenant（仅 super admin）
- 创建 tenant（slug / name / plan）
- 启用/停用 + 编辑 metadata

详见 [`multi-tenant-deliverable.md`](multi-tenant-deliverable.md) §M3。

---

## 9. Dashboard MVP (E 完成)

`apps/api/app/routers/dashboard.py` (3 端点) + 前端 `dashboard/{fin,hr,shared}/page.tsx`：

| 端点 | 域检查 | 数据源 | 失败行为 |
|---|---|---|---|
| `GET /api/dashboard/fin` | FINANCE view 必填 | manifest kpis.fin_view + shared_view | 无 FIN 权限 → 403 |
| `GET /api/dashboard/hr` | HR view 必填 | manifest kpis.hr_view + shared_view | 无 HR 权限 → 403 |
| `GET /api/dashboard/shared` | 无 | manifest kpis.shared_view | 200 + 空数组 |

**`PerspectiveSwitcher` 组件**：
- 位置：Topbar 右上角
- 选项：fin / hr / line_owner / admin / auditor / viewer / none
- 触发：写 `X-Active-View` header；后端写 `CurrentUserV2.active_view`
- 影响：Copilot prompt、audit log（标记 `active_view` 字段）、UI 导航 badge

**X-Active-View 透传链**：
```
BFF (Next.js) — 读 cookie.user_role → 决定默认 view
  ↓
fetch('/api/dashboard/fin', { headers: { 'X-Active-View': view } })
  ↓
后端 get_current_user_v2 — 解析 header → 写 active_view
  ↓
routers/dashboard.py — 审计日志带 active_view 标签
  ↓
audit_middleware — 写 raw.audit_log.active_view 列
```

详见 [`dashboard-deliverable.md`](dashboard-deliverable.md)。

---

## 10. 跨业务线汇总 (G 完成)

`apps/api/app/routers/cross_line_summary.py` (2 端点)：

| 端点 | 域 | 跨线累计 |
|---|---|---|
| `GET /api/finance/summary?lines=*` | finance | totals 累加（sum），rate 类（IRR / 坪效 / 人均营收）→ **null** |
| `GET /api/hr/summary?lines=residential,retail` | hr | 同上 |

**`?lines=` 解析**：
- 缺省 / `*` / `all` → 用户可见的全部业务线
- csv（如 `residential,retail`）→ 该 csv
- 空 → 用户可见的全部业务线

**域隔离**：hr_bp 调 `/api/finance/summary` → **403**（铁律）。fin_bp 调 `/api/hr/summary` → 同样 403。

**line-scoped 用户**：如 `fin_bp(residential)` 调 `/api/finance/summary?lines=*` — `*` 自动降级到只该用户可见的 `residential`（**不**返回其他线）。

详见 [`cross-line-summary-deliverable.md`](cross-line-summary-deliverable.md)。

---

## 11. Migration runner (F 完成)

`apps/api/app/db/migration_runner.py` 核心 + `apps/api/app/routers/migrations.py` 3 端点：

| 端点 | 用途 | 鉴权 |
|---|---|---|
| `GET /api/admin/migrations/status` | 列出 pending / applied / drift | admin |
| `POST /api/admin/migrations/apply` | 跑全部 pending（带 `pg_advisory_xact_lock` 串行化） | super admin |
| `POST /api/admin/migrations/verify` | SHA256 checksum 验证（drift 检测） | super admin |

**核心特性**：

- **顺序**：filename lexical (`001_xxx.sql` → `002_xxx.sql` → …)
- **并发**：`pg_advisory_xact_lock(migration_runner_lock_v1)` 防两个 API 实例同时跑
- **Drift 检测**：on-disk SHA256 vs `schema_migrations.checksum` 不一致 → 标 `drift`，**不**重跑（防 tamper）
- **Idempotency**：每个 migration 文件必须自带 `IF NOT EXISTS` / `ON CONFLICT DO NOTHING`；runner 拒绝重跑已 applied 文件
- **多 SQL 段**：检测 `BEGIN;` / `COMMIT;` 标记自动剥离（避免 outer transaction 结束后的内层 COMMIT 报错）

**4 份 migration**（已全部 apply）：

| # | 文件 | 用途 | 状态 |
|---|---|---|---|
| 001 | `001_rbac_v2.sql` | `user_roles` 加 `scope` / `line_id` + backfill | applied |
| 002 | `002_placeholder.sql` | 验证多文件处理 | applied |
| 003 | `003_multi_tenant_setup.sql` | tenants + 6 表 tenant_id + RLS + tenant_lock | applied |
| 004 | `004_tenant_m2_super_admin_and_triggers.sql` | is_super_admin + BEFORE INSERT 触发器 | applied |

详见 [`migration-runner-deliverable.md`](migration-runner-deliverable.md)。

---

## 12. 测试覆盖

**277 passed / 0 failed**（累计）：

| 模块 | 测试数 | 文件 |
|---|---|---|
| v2 RBAC unit (8 角色 × 5 域 × 读/写) | 145 | `tests/test_rbac_v2.py` |
| Admin v2 roles (binding CRUD) | 16 | `tests/test_admin_v2_roles.py` |
| Admin business-line editor | 19 | `tests/test_admin_business_lines.py` |
| Dashboard MVP (fin/hr/shared) | 23 | `tests/test_dashboard.py` |
| Migration runner (status/apply/verify) | 12 | `tests/test_migration_runner.py` |
| Cross-line summary | 34 | `tests/test_cross_line_summary.py` |
| Multi-tenant M1 | 10 | `tests/test_multi_tenant_m1.py` |
| Tenant context M2 | 7 | `tests/test_tenant_context.py` |
| Admin tenants M3 | 11 | `tests/test_admin_tenants.py` |

**v0.1.0 回归**：
- 145 单元 + 60 集成测试**仍绿**（向后兼容不破坏）
- 37 passed / 11 skipped (DB-gated) 仍 PASS

---

## 13. 用例 (curl 演示)

### 13.1 super admin 切 tenant + 列业务线

```bash
# 1. login
curl -c /tmp/c.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
# → 200 + Set-Cookie: finbp_token=...

# 2. 列 tenants
curl -s -b /tmp/c.txt http://localhost:18000/api/admin/tenants | jq '.tenants | length'
# → 1 (default)

# 3. 看自己 tenant
curl -s -b /tmp/c.txt http://localhost:18000/api/auth/me-tenant | jq .
# → { "tenant_id": "00000000-0000-...", "is_super_admin": true }

# 4. 切到 default tenant
curl -s -b /tmp/c.txt -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000000" \
  http://localhost:18000/api/registry/lines | jq '.lines | length'
# → 9
```

### 13.2 fin_bp_global 跨业务线汇总

```bash
# 1. login
curl -c /tmp/c.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"finbp-global","password":"<set>"}'

# 2. 跨业务线 finance 汇总
curl -s -b /tmp/c.txt http://localhost:18000/api/finance/summary?lines=* | jq .
# → { "lines": ["residential","retail",...], "totals": { "revenue": 12345, ... } }
```

### 13.3 fin_bp(hr) → /api/finance/summary → 403

```bash
# hr_bp 角色调 finance summary → 物理隔离
curl -s -b /tmp/c.txt -o /dev/null -w "%{http_code}\n" \
  http://localhost:18000/api/finance/summary
# → 403
```

### 13.4 视角切换 → Copilot prompt 变化

```bash
# fin 视角
curl -s -b /tmp/c.txt -H "X-Active-View: fin" \
  -X POST http://localhost:18000/api/copilot/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"本月营收"}' | jq '.used_perspective'
# → "fin"

# hr 视角
curl -s -b /tmp/c.txt -H "X-Active-View: hr" \
  -X POST http://localhost:18000/api/copilot/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"本月营收"}' | jq '.used_perspective'
# → "hr"
# 答：'该数据不属于 HR 视角访问范围'
```

---

## 14. 升级路径 — 旧 v1 用户自动 backfill

| v1 角色 | v2 binding | 自动 |
|---|---|---|
| `admin` | `Role.ADMIN` + `Scope.GLOBAL` | ✅ |
| `auditor` | `Role.AUDITOR` + `Scope.GLOBAL` | ✅ |
| `viewer` | `Role.VIEWER` + `Scope.GLOBAL` | ✅ |
| `bp:residential` | `Role.LINE_OWNER` + `Scope.BUSINESS_LINE` + `line_id="residential"` | ✅ (保守映射) |
| `bp:retail` | `Role.LINE_OWNER` + `Scope.BUSINESS_LINE` + `line_id="retail"` | ✅ |
| ... 7 个 BP 用户 | 同上 | ✅ |

`infra/migrations/001_rbac_v2.sql` 第 4 段 backfill SQL 自动跑，**业务无感**。admin 在 admin UI 把 `line_owner` 细分成 `fin_bp` / `hr_bp` 是 P1 follow-up。

---

## 15. 文件路径速查

| 模块 | 路径 |
|---|---|
| 8 角色 RBAC 核心 | `apps/api/app/core/rbac_v2.py` |
| CurrentUserV2 + 视角切换 | `apps/api/app/core/auth_v2.py` |
| 旧 v1 守卫（保留） | `apps/api/app/core/rbac.py` (新增 `require_super_admin_dep`) |
| Dashboard 路由 | `apps/api/app/routers/dashboard.py` |
| 跨线汇总路由 | `apps/api/app/routers/cross_line_summary.py` |
| Admin 业务线编辑 | `apps/api/app/routers/admin_business_lines.py` |
| Migration runner 核心 | `apps/api/app/db/migration_runner.py` |
| Migration runner 路由 | `apps/api/app/routers/migrations.py` |
| 4 份 migration | `infra/migrations/{001,002,003,004}_*.sql` |
| Manifest v2 模板 | `business_lines/_template/manifest.yaml.v2.example` |
| Manifest v2 示范 | `business_lines/project-management/manifest.yaml` |
| 前端 Topbar 视角切换 | `apps/web/app/(dashboard)/_components/PerspectiveSwitcher.tsx` |
| 前端 Admin 用户 v2 | `apps/web/app/(dashboard)/admin/users/page.tsx` |
| 前端 Admin 业务线编辑 | `apps/web/app/(dashboard)/admin/business-lines/[id]/page.tsx` |
| 前端 Dashboard FIN | `apps/web/app/(dashboard)/dashboard/fin/page.tsx` |
| 前端 Dashboard HR | `apps/web/app/(dashboard)/dashboard/hr/page.tsx` |
| 前端 Dashboard Shared | `apps/web/app/(dashboard)/dashboard/shared/page.tsx` |

---

## 16. Follow-up (P1 / P2)

### P1 — v2.1

- **业务线 admin 升级 v2**（除 project-management 外 8 条）：复制 `data_scope` / `access_matrix` / `kpis` 块
- **registry.py line-guard 升级**：从 `can_view_line` 改用 `can_access_domain(line_id, DataDomain.X, write)`
- **Copilot SQL tenant 化**：`copilot_engine.py` 的 `_fetch_line_data` 走 `tenant_session(ctx.tenant_id)`
- **alerts/forecast/sensitivity/copilot** 4 个通用 engine 的 SQL 全走 `tenant_session` + 域检查

### P2 — 商业化前

- `is_super_admin` 升/降级 UI（admin UI 加 toggle）
- per-tenant 业务线绑定（一个 tenant 不一定拥有全部 9 条业务线）
- 多租户 UI dashboard（super admin 看所有 tenant 汇总）
- AI Prompt 管理 UI（详见 `AI-DATA-BEAUTICIAN-REQUIREMENTS.md`）

---

_交付日期: 2026-09-04 / 阶段: PR #1 合并 master / 累计测试: 277 passed / 0 failed_
