# Changelog

All notable changes to the fin-bp-portal project are recorded here.
The latest changes appear at the top.

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
- **配置**: `.env.example` 加 `JWT_SECRET` / `JWT_ALGORITHM` / `JWT_EXPIRY_HOURS` / `FIN_BP_BOOTSTRAP_ADMIN_*` / `FIN_BP_COOKIE_*`; `docker-compose.yml` 把这些注入 `api` 服务。
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
