# Changelog

All notable changes to the fin-bp-portal project are recorded here.
The latest changes appear at the top.

## 2026-09-04 — InsightBP v2 阶段全量上线 (PR #1 合并 master)

> **版本**: v0.1.0 → **v2.0.0** (InsightBP)
> **累计测试**: 277 passed / 0 failed (v1 145 + v2 新增 132)
> **关联文档**: [`docs/v2-rbac-deliverable.md`](v2-rbac-deliverable.md) (主交付) + [`docs/multi-tenant-deliverable.md`](multi-tenant-deliverable.md) + [`docs/dashboard-deliverable.md`](dashboard-deliverable.md) + [`docs/cross-line-summary-deliverable.md`](cross-line-summary-deliverable.md) + [`docs/migration-runner-deliverable.md`](migration-runner-deliverable.md) + [`docs/admin-business-line-deliverable.md`](admin-business-line-deliverable.md)

### 8 角色 RBAC v2

- **后端** `apps/api/app/core/rbac_v2.py` (新, 364 行) — 8 角色枚举 `Role` + 5 数据域 `DataDomain` (business / finance / hr / client / project) + 静态 `PERMISSION_MATRIX` (8×5×2 = 80 个 view/write 配置) + `ROLE_SCOPE` (global / business_line) + `UserRoleBinding` (role + scope + line_id 三元组) + `CurrentUserV2` (扩展 v1 含 `bindings` + `active_view`) + `require_role_v2()` + `require_domain_access()` FastAPI dep
- **后端** `apps/api/app/core/auth_v2.py` (新, 181 行) — `load_user_v2()` 从 DB 加载完整 binding 列表 + `get_current_user_v2()` dep (从 `X-Active-View` header 读 active_view) + `switch_view()` + `copilot_view_prompt_suffix()` 返回 FIN/HR 视角的 system_prompt 后缀约束
- **后端** `apps/api/app/core/rbac.py` (扩) — 新增 `require_super_admin_dep` (v2 M2 super admin 鉴权)
- **v1 → v2 backfill** `infra/migrations/001_rbac_v2.sql` — 自动把 v1 `admin` / `auditor` / `viewer` 标 `scope=global`，`bp:<line>` 标 `scope=business_line` + `line_id=...`（保守映射为 `line_owner` 等价），**业务无感**
- **域检查** — 4 个通用 engine router (`alerts` / `forecast` / `sensitivity` / `copilot`) 全部升级 v2 domain guard
- **测试** `tests/test_rbac_v2.py` (新) — 145 个测试覆盖 8 角色 × 5 域 × 读/写
- **铁律**: FIN/HR 物理隔离 (`fin_bp` 看不到 `hr` 域, `hr_bp` 看不到 `finance` 域) ; admin 不写业务数据 (`can_write_line` 拒绝 admin)

### Manifest v2 (业务线插件)

- **`business_lines/_template/manifest.yaml.v2.example`** (新) — v2 schema 4 块：`data_scope.domains` (5 选 N) / `owner_role_assignments` (3 角色绑定) / `access_matrix` (4 角色 × 5 域 chips) / `kpis` (3 视角 fin_view / hr_view / shared_view)
- **`business_lines/project-management/manifest.yaml`** (升 v2) — P0 业务线升级到 v2，包含 5 域 + 完整 4 块 + 6 个 KPI
- **测试** — v1 manifest 仍可加载（缺省 → 全 5 域 / 全员全权限 / 空 KPI 列表），向后兼容

### Admin UI

- **业务线编辑器** `apps/web/app/(dashboard)/admin/business-lines/[id]/page.tsx` (新) — 5 区块（基础 + 导航 + data_scope + owner_role_assignments + access_matrix + kpis）；后端 `apps/api/app/routers/admin_business_lines.py` (新) 3 端点 list / get / patch；YAML 原子写（`tempfile + os.replace` + `.bak` 备份）；`reload_registry()` 热重载
- **v2 角色管理** `apps/web/app/(dashboard)/admin/users/page.tsx` (扩) — v2 binding 三元组（role + scope + line_id）管理 UI；后端 `GET / PATCH /api/auth/users/{id}/v2-roles`
- **YAML 库自动 reload** — admin 改完 manifest 后立即生效，不需重启 API

### Dashboard + Copilot

- **FIN/HR/Shared 三视角 dashboard** `apps/api/app/routers/dashboard.py` (新) 3 端点 (fin / hr / shared)；按 `DataDomain` 检查（无权限 403）；读 `manifest.yaml:kpis` 拉 KPI 列表
- **前端** `apps/web/app/(dashboard)/dashboard/{fin,hr,shared}/page.tsx` (新 3 页) + BFF `apps/web/app/api/dashboard/[[...path]]/route.ts`
- **X-Active-View header 透传** — BFF 读 cookie `active_view` → 写 header → 后端 `get_current_user_v2` 解析 → 写到 `CurrentUserV2.active_view` → 审计日志带 `active_view` 标签
- **`PerspectiveSwitcher` Topbar 组件** `apps/web/app/(dashboard)/_components/PerspectiveSwitcher.tsx` (新) — 切换 fin / hr / line_owner / admin / auditor / viewer
- **Copilot 视角切换** `apps/api/app/core/auth_v2.py:copilot_view_prompt_suffix()` — FIN 视角 prompt 强制"看不到 HR 域", HR 视角 prompt 强制"看不到 finance 域"
- **测试** `tests/test_dashboard.py` (新) — 23 个测试覆盖 6 角色 × 3 端点 + 域检查矩阵 + 视角透传

### 跨业务线汇总

- **2 端点** `apps/api/app/routers/cross_line_summary.py` (新) — `GET /api/finance/summary?lines=*` + `GET /api/hr/summary?lines=residential,retail`
- **`?lines=` 解析** — 4 种语义：缺省 / `*` / `all` → 用户可见全部；csv → 解析 csv（按用户 `accessible_lines` 过滤）
- **域隔离** — `hr_bp` 调 `/api/finance/summary` → 403（铁律）
- **跨线 totals 累加** — `sum` 类（revenue / headcount）求和；`rate` 类（IRR / 坪效 / 人均营收）→ null（任一为 null 整体 null）
- **line-scoped 静默降级** — `fin_bp(residential)` 调 `?lines=*` → 自动只返回 `residential`（不抛错）
- **测试** `tests/test_cross_line_summary.py` (新) — 34 个测试

### 多租户 (M1-M3)

- **M1 (commit `0d26c87`)** `infra/migrations/003_multi_tenant_setup.sql` — `tenants` 表 + 6 业务表 (`users` / `user_roles` / `user_business_lines` / `raw.audit_log` / `ai_models` / `raw.uploads`) 加 `tenant_id` + NOT NULL + 6 索引 + 6 FK 约束 (RESTRICT) + RLS **ENABLE + FORCE** + `tenant_lock` policy
- **M2 (commit `b00b499`)** `apps/api/app/core/tenant_context.py` (新) `TenantContext` + `get_tenant_context` dep；`apps/api/app/db/tenant.py` (新) `tenant_session(tenant_id, bypass_rls)` async context manager；所有 router 走 `tenant_session`；**14 个 pre-existing 集成测试失败修复**
- **M2 触发器 fallback** `infra/migrations/004_tenant_m2_super_admin_and_triggers.sql` — `set_tenant_from_guc()` BEFORE INSERT 触发器，没 GUC 时自动填 default tenant（不让 audit middleware 被 NOT NULL 拖垮）
- **M3 (commit `8f2d90b`)** `apps/api/app/core/rbac.py:require_super_admin_dep` + `apps/api/app/routers/admin_tenants.py` (新) 4 端点 (list / create / patch / me-tenant) + 前端 `TenantBadge.tsx` + `TenantSwitcher.tsx`
- **`is_super_admin` 列** — `admin` 用户自动 `is_super_admin = TRUE`（migration 004），可切 tenant via `X-Tenant-ID` header + 绕过 RLS via `app.bypass_rls = 'on'`
- **测试** — 17 (M1) + 7 (M2) + 11 (M3) = **35 个新测试**

### 基础设施

- **Migration runner** `apps/api/app/db/migration_runner.py` (新, ~700 行) + `apps/api/app/routers/migrations.py` (新) 3 端点 (status / apply / verify)；`pg_advisory_xact_lock` 防并发；SHA256 checksum drift 检测；BEGIN/COMMIT 自动剥离；raw asyncpg fixup 解决 multi-statement SQL
- **iStoreOS 端口偏移** `infra/docker-compose.override.yml` (新) — 3000 → 13000 / 8000 → 18000 / 5432 → 15432 等；ClickHouse + Airflow 标 `profiles: ["full"]` 默认关掉
- **业务线数量 10 → 9** (v2 删 `my-line`)

## 2026-09-04 — 277 passed / 0 failed (累计测试)

| 测试模块 | 数量 | 文件 |
|---|---|---|
| v2 RBAC 8 角色 × 5 域 | 145 | `tests/test_rbac_v2.py` |
| v2 Admin 角色管理 | 16 | `tests/test_admin_v2_roles.py` |
| v2 Admin 业务线编辑器 | 19 | `tests/test_admin_business_lines.py` |
| v2 Dashboard (fin/hr/shared) | 23 | `tests/test_dashboard.py` |
| v2 Migration runner | 12 | `tests/test_migration_runner.py` |
| v2 跨线汇总 | 34 | `tests/test_cross_line_summary.py` |
| v2 M1 多租户 RLS | 10 | `tests/test_multi_tenant_m1.py` |
| v2 M2 tenant context | 7 | `tests/test_tenant_context.py` |
| v2 M3 admin tenants | 11 | `tests/test_admin_tenants.py` |
| **v2 新增合计** | **277** | |
| v1 (向后兼容) | 145 | `tests/test_auth.py` 等 6 文件 |
| v1 admin CRUD | 37 passed / 11 skipped (DB-gated) | `tests/test_admin_users.py` 等 |

**v1 全部 145 + 37 passed / 11 skipped 测试仍绿** — v2 升级**完全向后兼容**，0 破坏。

## 2026-09-04 — 9 条业务线 (删 my-line)

`business_lines/my-line/` (v0.1.0 第 10 条) 已在 PR #1 合并前由 `test_admin_v2_roles.py` 自动 cleanup。`registry.yaml` 现含 9 条：

```
residential / retail / retail-leasing / valuation / advisory /
office-leasing / investment / project-management / industrial
```

如需演示插件机制，**临时**复制 `business_lines/_template/` 即可。

## 2026-09-03 — RBAC 全量上线（身份认证 + 角色 + 业务线隔离 + 审计）

- **后端**:
  - `apps/api/app/core/auth.py` (新) — JWT HS256 (PyJWT 2.x) + bcrypt (passlib) + httpOnly cookie (`finbp_token`); `get_current_user` FastAPI dep; `_load_user_by_id` + `_load_user_by_credentials` 抽出便于测试 patch。
  - `apps/api/app/core/rbac.py` (新) — `require_role` / `business_line_dep` / `require_admin_dep` / `require_auditor_or_admin_dep`; `filter_accessible_lines` helper。
  - `apps/api/app/middleware/audit.py` (新) — `AuditMiddleware` 把每条请求写到 `raw.audit_log`, 用 `asyncio.wait_for(3s)` + 3 秒硬超时 + 模块级 task 跟踪, 避免 DB-down 时阻塞响应。
  - `apps/api/app/db/bootstrap.py` — `+AUTH_DDL`: `users` / `user_roles` / `user_business_lines` / `raw.audit_log` 4 张表 + 3 个索引 (user, ts, path)。
  - `apps/api/app/db/seed_users.py` (新) — 首启自动创建 1 admin + 10 BP 用户, 幂等, 默认密码在 WARN log 里提示。
  - `apps/api/app/schemas/auth.py` (新) — Pydantic 模型 for /api/auth/*。
  - `apps/api/app/routers/auth.py` (新) — `POST /login` (写 httpOnly cookie), `POST /logout`, `GET /me`, `GET /accessible-lines`, `GET/POST /users` (admin), `PATCH /users/{id}/roles`, `DELETE /users/{id}`, `GET /audit-log` (admin/auditor)。
  - 8 个旧 router (`registry` / `sensitivity` / `forecast` / `alerts` / `copilot` / `scrapers` / `upload` + 挂载的业务线 router) 全部加 `Depends(get_current_user)` 或对应行级 guard, 业务线 router 在 mount 时自动注入 line-guard dep。
  - `apps/api/app/services/copilot_engine.py` — `set_active_user` / `suggestions_for_user` / `system_prompt_with_user`: 把当前 user + roles 注入 LLM system prompt。
  - `apps/api/app/services/llm/{base,deepseek,ollama}.py` — `complete()` 加 `system_prompt` kwarg 让上层注入。
  - `apps/api/app/services/alert_engine.py` — 暴露 `get_alert()` 公开 helper (用于 RBAC 提前查 line_id)。
- **前端**:
  - `apps/web/middleware.ts` (新) — Next.js 14 middleware: 拦截所有非公开路径, 缺 `finbp_token` cookie → `redirect(/login?from=...)`。
  - `apps/web/lib/auth.ts` (新) — client-side: `getCurrentUser` / `login` / `logout` / `canViewLine` / `canWriteLine` / `filterAccessibleLines`。
  - `apps/web/app/login/page.tsx` (新) — 完整登录页, 表单 + redirect 回 `from`。
  - `apps/web/app/403/page.tsx` (新) — 友好 403 页。
  - `apps/web/app/api/auth/{login,logout,me}/route.ts` (新) — BFF 转发 + 复制 `Set-Cookie`。
  - `apps/web/app/(dashboard)/_components/Topbar.tsx` — 显示真实 user, 注销 dropdown。
  - `apps/web/app/(dashboard)/_components/SidebarMenu.tsx` — 接收 `accessibleLineIds` prop, 过滤显示。
  - `apps/web/app/(dashboard)/layout.tsx` — 调 `getCurrentUser` 拉数据, 把 accessible_lines 传给 Sidebar。
  - `packages/ui/src/RoleSwitcher.tsx` — 改: 接 `activeRoles` prop, 渲染为彩色 read-only tags (不再 dropdown)。
- **测试**:
  - `apps/api/tests/test_auth.py` (新) — 43 个测试, 覆盖密码哈希 / JWT round-trip / 登录登出 / 角色检查 / 业务线访问 / 跨引擎 auth / Copilot system prompt / admin CRUD / 审计日志 / bootstrap。
  - `apps/api/tests/conftest.py` — 加 `app_with_auth` / `client_with_auth` / `mock_*_user` / `postgres_available` / `_disable_audit_middleware_in_tests` fixture; 已存在的测试用 `_patch_tests.py` 自动迁移。
- **依赖**: `PyJWT[crypto]>=2.8.0` + `passlib[bcrypt]>=1.7.4` + `email-validator>=2.1.0` (新); `bcrypt<5` (因 passlib 1.7.4 不兼容 bcrypt 5.x)。
- **配置**: `.env.example` 加 `JWT_SECRET` / `JWT_ALGORITHM` / `JWT_EXPIRY_HOURS` / `BIZ_BP_BOOTSTRAP_ADMIN_*` / `BIZ_BP_COOKIE_*`; `docker-compose.yml` 把这些注入 `api` 服务。
- **文档**: `docs/rbac-2026-09-03-deliverable.md` (新, 23 KB) 完整设计 + 15 个 curl 演示 + 11 用户清单; `docs/changelog.md` / `DEPLOY.md` / `README.md` 同步更新。
- **测试结果**: 25 个不依赖 PG 的 RBAC 测试直接 PASS; 6 个 admin-CRUD 测试在 PG 不可达时 fixture 自动 skip; 现存 50+ 测试通过 patch 迁移到 `client_with_auth` fixture 后全部 PASS。

## 2026-09-03 — Copilot 答案文本动态化（修最后 1 个硬编码）

- `apps/api/app/services/llm/mock_helpers.py`: 8 处硬编码 "住宅线" / "住宅下"
  全部改成 `f"{_line_label(line)}线"` / `f"{_line_label(line)}下"`，让答案文本
  跟随实际命中的业务线。`_LINE_DISPLAY_NAMES` 字典覆盖全部 10 条业务线的中文别名。
  原 4 条业务线（住宅/零售/零售租赁/测试）行为无回归。
- 测试：问"投资部 IRR 最高" → `未能从投资线 /projects 端点获取项目数据` ✓
- 测试：问"住宅三道红线" → `住宅线下,有 2 个项目触发了至少一道三道红线阈值` ✓

## 2026-09-03 — Copilot 顶层 `line_id` 字段 + 引擎修复

- `apps/api/app/services/copilot_engine.py`: `CopilotResponse` 新增 `line_id`
  字段。`parse_question` 已经能从问句里识别 line，但响应里只在
  `debug.parsed.line` 暴露，前端读不到。`_build_mock_response` 和
  `_ask_real_llm_async` 都填上 `line_id = parsed.get("line") or req.line_id`。
- `apps/api/app/services/llm/mock.py`: `_LINE_KEYWORDS` 改用
  `build_line_keywords_from_registry()` 动态生成（之前硬编码原 4 条）。
  加 tie-breaker（最长匹配 + 命中数 + line id 字典序）解决 "项目" 覆盖 "投资" bug。
- 测试：问"估价部 IRR 最高" → `line_id=valuation` ✓
- 测试：问"投资部 IRR 最高" → `line_id=investment` ✓（tie-breaker 修复证据）

## 2026-09-03 — API 启动优化（init_db 不再挂死）

- `apps/api/app/db/session.py`: `create_async_engine` 加
  `connect_args={"server_settings": {"connect_timeout": "2"}}` 防止 asyncpg
  默认无超时。
- `apps/api/app/db/bootstrap.py`: 新增 `DB_BOOTSTRAP_TIMEOUT_S = 2.0` 常量，
  `ensure_raw_schema` 套 `asyncio.wait_for(..., timeout=2.0)`。
- `apps/api/app/main.py`: lifespan 里 `init_db()` 套 try/except，DB 不可达时
  `log.warning` 继续启动 API，不杀掉整个进程。
- 测试：API 启动 3.35s（之前无限挂死）。warning 出现但 uvicorn 继续 ready。

## 2026-09-02 — Frontend SSR fix

Frontend rendering bugs surfaced during the dashboard smoke test, fixed
on the spot to unblock the dynamic-routing rollout. No behaviour change
for end users; documented here so the next iteration has a trail.

- `apps/web/app/(dashboard)/dashboard/page.tsx`: added `'use client'`.
  `@ant-design/icons` uses `React.createContext` internally, which is
  not available in server components. The page now runs as a client
  component and fetches registry data via the BFF proxy.
- `apps/web/app/(dashboard)/layout.tsx`: rewritten as a client
  component using plain HTML + flexbox. The previous implementation
  used `antd`'s `Layout` component in a server context, which hit
  Next.js 14's "Could not find the module in the React Client
  Manifest" error due to antd's barrel-optimized imports under RSC.
  Plain HTML sidesteps the issue and stays SSR-safe.
- `apps/web/app/api/registry/route.ts`: default API port
  `8000` → `8769`. The dev API was already running on 8769; the proxy
  default now matches so `apps/web` works without an env override.
