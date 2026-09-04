# MAINTENANCE — InsightBP / Biz-BP Portal 维护与交接手册

> **仓库**：<https://github.com/Njryadmin/biz-bp-portal>（private）
> **本地路径**：`C:\Users\mozzi\.mavis\workspace\biz-bp-portal`
> **远程 origin**：`https://github.com/Njryadmin/biz-bp-portal.git`
> **本手册最近一次更新**：2026-09-04（v2.0.0 — InsightBP 阶段）

---

## TL;DR

InsightBP (Biz-BP Portal v2) 是一个面向房地产咨询公司的**可插拔式"业务合伙人"分析门户**。
**后端**是 FastAPI（`apps/api/`），**前端**是 Next.js 14 + Ant Design 5（`apps/web/`），
业务线代码完全隔离在 `business_lines/<line>/` 目录下，**新增业务线 = 0 行核心代码改动**。
**v2 阶段 (PR #1 合并 master, 2026-09-04)** 升级为 **8 角色 RBAC + 5 数据域 + 多租户 (M1-M3) + manifest v2**。
所有 RBAC v2 (JWT + httpOnly cookie + 域检查)、多租户 (RLS + tenant context)、Admin UI、AI 模型注册表、3 个真实数据爬虫、4 个通用引擎
（敏感性 / 预测 / 告警 / Copilot）均已上线，**默认就绪**。**277 passed / 0 failed**。

如需寻求帮助，先看 [§6 常见操作](#6-常见操作) 和 [§10 已知陷阱](#10-已知陷阱)。
新增业务线请按 [§7 扩展系统](#7-扩展系统) 走 5 步流程（v2 manifest 4 块必填）。

---

## 1. 项目一览

| 维度 | 值 |
|---|---|
| **业务领域** | 房地产咨询（住宅 / 零售 / 投资 / 写字楼 / 工业等） |
| **后端** | Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 async + asyncpg |
| **前端** | Next.js 14 (App Router) + TypeScript 5 + Ant Design 5 + ECharts 5 |
| **数据库** | PostgreSQL 16（生产 = compose；本地 = `pgserver_runner.py` 嵌入式实例） |
| **依赖** | Airflow 2.8 / Redis 7 / ClickHouse 24 / MinIO（生产可选） |
| **仓库结构** | 单一 monorepo（`apps/` + `business_lines/` + `packages/` + `infra/` + `docs/`） |
| **业务线数量** | 9（`residential` / `retail` / `retail-leasing` / `valuation` / `advisory` / `office-leasing` / `investment` / `project-management` / `industrial`）— **v2 删 my-line** |
| **当前版本** | **v2.0.0**（InsightBP — post PR #1 merge, 2026-09-04）|
| **多租户** | **M1-M3 已落地**：tenants 表 + RLS + tenant context middleware + super admin UI |
| **代码规模** | ~20k LOC Python + ~4k LOC TypeScript（含 9 业务线 + RBAC v2 + 多租户 + Admin UI） |

### 1.1 关键 URL（本地 dev）

| 服务 | 地址 | 说明 |
|---|---|---|
| Web | <http://127.0.0.1:3000> | Next.js 开发服务器 |
| API | <http://127.0.0.1:8769> | FastAPI/uvicorn |
| Postgres | `127.0.0.1:11667` | 嵌入式 `pgserver` |
| API 文档 | <http://127.0.0.1:8769/docs> | FastAPI 自动生成的 Swagger |
| 登录 | <http://127.0.0.1:3000/login> | 用户名 `admin` / 密码 `admin123`（首次启动默认值） |
| v2 关键端点 | `/api/auth/me-v2` `/api/auth/me-tenant` `/api/dashboard/{fin,hr,shared}` `/api/finance/summary` `/api/hr/summary` `/api/admin/business-lines` `/api/admin/tenants` `/api/admin/migrations/status` | v2 新增端点（详见 `docs/v2-rbac-deliverable.md`） |

### 1.2 关键 URL（生产 docker compose）

**标准 compose**（默认端口）：

| 服务 | 地址 | 默认凭据 |
|---|---|---|
| Web | <http://localhost:3000> | — |
| API | <http://localhost:8000> | — |
| Airflow | <http://localhost:8080> | `admin` / `admin` |
| MinIO 控制台 | <http://localhost:9001> | `finbp` / `finbp12345` |
| Postgres | `localhost:5432` | `finbp` / `finbp` |
| ClickHouse | `localhost:8123` | `finbp` / `finbp` |

**iStoreOS 端口偏移 compose**（用 `infra/docker-compose.override.yml`）：

| 服务 | 默认端口 | 偏移后端口 | 备注 |
|---|---|---|---|
| Web | 3000 | **13000** | 避 moontv-core (3000) |
| API | 8000 | **18000** | |
| Airflow | 8080 | 18080 | profiles=["full"] |
| MinIO | 9000/9001 | 19000/19001 | |
| Postgres | 5432 | 15432 | |
| Redis | 6379 | 16379 | |
| ClickHouse | 8123/9100 | 18123/19100 | profiles=["full"] |

启动命令（iStoreOS）：

```powershell
docker compose -f infra\docker-compose.yml -f infra\docker-compose.override.yml --env-file .env up -d --build
```

详细部署参见 [`DEPLOY.md`](DEPLOY.md) §3 / §iStoreOS 段。

---

## 2. 仓库目录树

下面是"对维护者最重要的"文件/目录的一览。完整 tree 请执行
`Get-ChildItem -Recurse -File`（排除 `node_modules/` 与 `__pycache__/`）。

```
biz-bp-portal/
├── README.md                       ← 5 分钟快速开始（中文）
├── DEPLOY.md                       ← Docker / 7 服务栈的生产部署（中文）
├── MAINTENANCE.md                  ← 你正在读的本文件
├── AGENTS.md                       ← AI 代理交接指南（Claude Code / Codex 等）
├── package.json                    ← npm workspace 根（web + packages）
│
├── apps/
│   ├── api/                        ← FastAPI 后端 (v2 17 routers)
│   │   ├── app/
│   │   │   ├── main.py             ← uvicorn 入口；lifespan 挂载业务线 + 初始化 DB + migration runner
│   │   │   ├── core/
│   │   │   │   ├── auth.py         ← v1 JWT + cookie (保留)
│   │   │   │   ├── auth_v2.py      ← v2 CurrentUserV2 + 视角切换 (NEW)
│   │   │   │   ├── rbac.py         ← v1 RBAC + require_super_admin_dep (v2 扩展)
│   │   │   │   ├── rbac_v2.py      ← v2 8 角色 × 5 域 PERMISSION_MATRIX (NEW)
│   │   │   │   ├── tenant_context.py ← v2 M2 多租户 dep (NEW)
│   │   │   │   ├── registry.py     ← 业务线 manifest 解析 + reload
│   │   │   │   ├── config.py / secret.py / logging.py
│   │   │   ├── db/
│   │   │   │   ├── bootstrap.py    ← DDL (SCHEMA / AUTH / AI_MODELS)
│   │   │   │   ├── seed_users.py   ← 1 admin + 9 BP user (v1 → v2 自动 backfill)
│   │   │   │   ├── tenant.py       ← v2 tenant_session helper (NEW)
│   │   │   │   └── migration_runner.py ← v2 F 任务核心 (NEW)
│   │   │   ├── middleware/         ← AuditMiddleware（重试一次写审计；v2 +active_view 字段）
│   │   │   ├── routers/            ← 17 个 APIRouter (v2 扩到 17)
│   │   │   │   ├── auth.py         ← v1 + me-v2 + me-tenant + v2-roles (v2 扩)
│   │   │   │   ├── dashboard.py    ← v2 3 端点 (NEW)
│   │   │   │   ├── cross_line_summary.py ← v2 2 端点 (NEW)
│   │   │   │   ├── admin_business_lines.py ← v2 在线编辑 (NEW)
│   │   │   │   ├── admin_tenants.py ← v2 M3 4 端点 (NEW)
│   │   │   │   ├── migrations.py   ← v2 F 任务 3 端点 (NEW)
│   │   │   │   ├── registry.py / sensitivity.py / forecast.py / alerts.py / copilot.py
│   │   │   │   └── upload.py / scrapers.py / ai_models.py
│   │   │   ├── schemas/            ← Pydantic v2 响应模型 (+ tenant/dashboard/cross_line_summary v2)
│   │   │   └── services/
│   │   │       ├── sensitivity_engine.py
│   │   │       ├── forecast_engine.py
│   │   │       ├── alert_engine.py
│   │   │       ├── copilot_engine.py (+ v2 视角 prompt)
│   │   │       ├── llm/            ← base + deepseek + ollama + mock + factory
│   │   │       ├── parsers/        ← CSV / Excel / 银行流水解析
│   │   │       └── scrapers/       ← base + registry + utils + 3 个真实爬虫
│   │   ├── pgserver_runner.py      ← 嵌入式 Postgres 控制脚本（端口 11667）
│   │   ├── pyproject.toml
│   │   └── tests/                  ← 277 pytest 用例 (v2 新增 132 个)
│   └── web/                        ← Next.js 14 前端
│       ├── app/
│       │   ├── (dashboard)/        ← 受保护页面（layout 要求 cookie）
│       │   │   ├── dashboard/      ← 总览 + v2 fin/hr/shared (NEW)
│       │   │   ├── sensitivity/    ← 通用敏感性 Lab
│       │   │   ├── copilot/        ← AI 问答 (v2 + 视角切换)
│       │   │   ├── forecast/       ← 滚动预测
│       │   │   ├── alerts/         ← 告警中心
│       │   │   ├── scrapers/       ← 爬虫面板
│       │   │   ├── admin/          ← 用户 / AI 模型 / 业务线 / 租户 (v2 +2)
│       │   │   └── [line]/         ← 动态业务线路由
│       │   ├── _components/        ← Topbar + PerspectiveSwitcher + TenantBadge + TenantSwitcher (v2 +3)
│       │   ├── api/                ← BFF 代理（/api/* → API:8769）
│       │   ├── login/, 403/        ← 公共页
│       │   └── layout.tsx
│       ├── middleware.ts           ← 全站 cookie 守卫
│       └── package.json
│
├── business_lines/                 ← 唯一的"业务线代码"区 (v2 9 条, 删 my-line)
│   ├── registry.yaml               ← 9 条业务线的清单（每次新增要 +1 行）
│   ├── _template/                  ← 5 步复制-修改模板 + manifest.yaml.v2.example (v2 4 块)
│   ├── residential/                ← 住宅分析
│   ├── retail/                     ← 零售分析
│   ├── retail-leasing/             ← 零售租赁
│   ├── valuation/                  ← 估价
│   ├── advisory/                   ← 顾问
│   ├── office-leasing/             ← 写字楼租赁
│   ├── investment/                 ← 投资
│   ├── project-management/         ← 项目管理 (v2 P0 升级, 5 域 + 4 v2 块齐全)
│   └── industrial/                 ← 工业地产
│
├── packages/
│   ├── types/                      ← 跨前端共享的 TS 类型（与 Pydantic 同步）
│   └── ui/                         ← UniversalKpiCard / UniversalChart / UniversalAgGrid
│
├── infra/                          ← 部署与编排
│   ├── docker-compose.yml          ← 7 服务栈
│   ├── docker-compose.override.yml ← v2 iStoreOS 端口偏移 (NEW, 3000→13000)
│   ├── migrations/                 ← v2 4 份 migration (001_rbac_v2 / 002_placeholder / 003_multi_tenant / 004_tenant_m2)
│   ├── airflow/dags/               ← ingest_daily + scrape_weekly
│   └── dbt/                        ← 全局 DBT 项目
│
├── docs/                           ← 全部交付文档 (v2 新增 6 份)
│   ├── README.md                   ← 文档索引
│   ├── architecture-overview.md     ← 5 张架构图
│   ├── v2-rbac-deliverable.md      ← v2 RBAC 完整设计 (NEW)
│   ├── multi-tenant-deliverable.md ← v2 M1-M3 多租户 (NEW)
│   ├── dashboard-deliverable.md    ← v2 E 任务 (NEW)
│   ├── cross-line-summary-deliverable.md ← v2 G 任务 (NEW)
│   ├── migration-runner-deliverable.md   ← v2 F 任务 (NEW)
│   ├── admin-business-line-deliverable.md ← v2 D1+D2 (NEW)
│   ├── rbac-2026-09-03-deliverable.md    ← v1 RBAC (superseded, 保留为历史)
│   ├── ai-models-deliverable.md
│   ├── admin-users-deliverable.md
│   ├── scrapers-deliverable.md
│   ├── ...                         ← 其它 v1 deliverable*.md
│   ├── plugin-howto.md             ← 5 步新增业务线（旧版，可作补遗）
│   └── maintenance/                ← 主题维护手册
│       ├── operations.md           ← 日常运维 (v2 + migration runner / 跨租户查询)
│       ├── extending.md            ← 扩展系统 (v2 + manifest v2 / v2 角色 / tenant / migration)
│       ├── troubleshooting.md      ← 故障排查
│       ├── architecture-decisions.md (v2 + 6 新决策)
│       └── conventions.md          ← 编码规范
│
├── data/landing/                   ← 落地区（上传 / 爬虫文件）
├── .env.example                    ← 环境变量模板
└── pyproject.toml / package.json   ← 顶层构建配置
```

---

## 3. 本地开发快速开始

> **前提**：Windows + PowerShell + Python 3.12 + Node.js 20+。**不需要** Docker，
> 嵌入式 `pgserver` 提供 dev DB。

### 3.1 启动顺序

```powershell
# 在仓库根目录打开 3 个独立 PowerShell 窗口，分别执行：

# --- 窗口 1：嵌入式 Postgres ---
cd 'C:\Users\mozzi\.mavis\workspace\biz-bp-portal'
python apps\api\pgserver_runner.py --bg    # 后台运行；日志在 .pgdata\postgresql.log
# 看到 "pgserver ready at 127.0.0.1:11667" 后再启动 API

# --- 窗口 2：API ---
cd 'C:\Users\mozzi\.mavis\workspace\biz-bp-portal'
$env:PYTHONPATH = "$(pwd)\apps\api"
python -m uvicorn app.main:app --app-dir apps\api --port 8769 --reload

# --- 窗口 3：Web ---
cd 'C:\Users\mozzi\.mavis\workspace\biz-bp-portal'
npm run web:dev
```

打开 <http://localhost:3000>，使用 `admin` / `admin123` 登录。

### 3.2 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| Postgres 在跑 | `python apps\api\pgserver_runner.py --status` | `ready=True` |
| API 启动 | `curl http://127.0.0.1:8769/healthz` | `{"status":"ok"}` |
| API 文档 | 浏览器打开 `/docs` | Swagger UI 正常 |
| 业务线注册 | `curl http://127.0.0.1:8769/api/registry/lines` | 401（未认证） |
| 登录 | 见 `docs/rbac-2026-09-03-deliverable.md` | 200 + `Set-Cookie: finbp_token=...` |
| Web 渲染 | 浏览器打开 `/` | 跳转 `/login` |

### 3.3 完整重启 pgserver

如果数据库被损坏（表错误、UUID 漂移等），执行：

```powershell
python apps\api\pgserver_runner.py --stop
python apps\api\pgserver_runner.py --reset      # 删除 .pgdata/ 整个数据目录
python apps\api\pgserver_runner.py --bg          # 重新 initdb + 启动
```

**注意**：`--reset` 会**永久删除**所有数据。生产 Postgres 数据卷不要用这个流程。

---

## 4. 生产部署

参见 [`DEPLOY.md`](DEPLOY.md) — 涵盖：
- 7 服务 Docker Compose 栈（web / api / postgres / redis / clickhouse / minio / airflow）
- 7+ 个环境变量的填表
- 密钥轮换（`JWT_SECRET` / `BIZ_BP_AI_SECRET_KEY` / `BIZ_BP_SERVICE_TOKEN`）
- TLS 终止建议
- 国产化替代（达梦 / TDengine / Ceph）

快速路径：

```powershell
cp .env.example .env
# 编辑 .env：DEEPSEEK_API_KEY / JWT_SECRET / POSTGRES_PASSWORD / 其它密钥
docker compose -f infra\docker-compose.yml --env-file .env up -d --build
```

---

## 5. 代码组织原则（每个文件/目录归谁管）

| 关注点 | 位置 | 备注 |
|---|---|---|
| **API 启动 / 路由挂载** | `apps/api/app/main.py:1` | uvicorn 入口；`lifespan` 调用 `mount_business_line_routers` + `init_db` + `seed_initial_users` + (v2) migration runner |
| **JWT / 认证 (v1)** | `apps/api/app/core/auth.py:1` | HS256 + bcrypt + httpOnly cookie；`get_current_user` 是所有路由的依赖 |
| **v2 认证 (CurrentUserV2)** | `apps/api/app/core/auth_v2.py:1` | v2 shape: bindings + active_view；`load_user_v2` + `get_current_user_v2` + `copilot_view_prompt_suffix` |
| **v1 RBAC 守卫** | `apps/api/app/core/rbac.py:1` | `require_role` / `business_line_dep` / `require_admin_dep` / `require_auditor_or_admin_dep` (+ v2 `require_super_admin_dep`) |
| **v2 RBAC 核心** | `apps/api/app/core/rbac_v2.py:1` | 8 角色枚举 + 5 数据域 + PERMISSION_MATRIX + `CurrentUserV2`; `require_domain_access()` FastAPI dep |
| **多租户 dep (v2 M2)** | `apps/api/app/core/tenant_context.py:1` | `TenantContext` dataclass + `get_tenant_context` dep (header / user_default / default 优先级) |
| **多租户 session (v2 M2)** | `apps/api/app/db/tenant.py:1` | `tenant_session(tenant_id, bypass_rls=False)` 包装 SQLAlchemy；自动 SET LOCAL GUC |
| **业务线注册表** | `business_lines/registry.yaml:1` + `apps/api/app/core/registry.py:1` | 1 行 + 1 个 YAML → 0 行核心代码；`reload_registry()` 热重载 (v2 写入立即生效) |
| **业务线动态加载** | `apps/api/app/routers/registry.py:1` | importlib 加载 `business_lines/<id>/api/router.py` |
| **审计中间件** | `apps/api/app/middleware/audit.py:1` | 写 `raw.audit_log`；重试一次 + 3s 超时；v2 +`active_view` 字段 + `tenant_id` 字段 |
| **DB 模式（DDL）** | `apps/api/app/db/bootstrap.py:1` | 3 组：`SCHEMA_DDL` / `AUTH_DDL` / `AI_MODELS_DDL`；幂等 |
| **首次启动用户** | `apps/api/app/db/seed_users.py:1` | admin + 9 BP user；v1 → v2 自动 backfill (bp:\<line\> → line_owner:\<line\>) |
| **Migration runner (v2 F)** | `apps/api/app/db/migration_runner.py:1` | `pg_advisory_xact_lock` + SHA256 checksum + drift 检测 + BEGIN/COMMIT 剥离 |
| **Dashboard (v2 E)** | `apps/api/app/routers/dashboard.py:1` | 3 端点 fin / hr / shared；按 `DataDomain` 检查；读 manifest kpis |
| **跨线汇总 (v2 G)** | `apps/api/app/routers/cross_line_summary.py:1` | 2 端点 finance / hr summary；`?lines=` csv/glob；rate 类 null |
| **Admin 业务线 (v2 D1)** | `apps/api/app/routers/admin_business_lines.py:1` | 3 端点 list / get / patch；YAML 原子写 + .bak + 热重载 |
| **Admin tenants (v2 M3)** | `apps/api/app/routers/admin_tenants.py:1` | 4 端点 list / create / patch / me-tenant；仅 super admin |
| **Migration 路由 (v2 F)** | `apps/api/app/routers/migrations.py:1` | 3 端点 status / apply / verify |
| **4 个通用引擎** | `apps/api/app/services/{sensitivity,forecast,alert,copilot}_engine.py` | 全部 0 业务线硬编码；v2 + 视角切换 |
| **LLM 后端** | `apps/api/app/services/llm/{base,deepseek,ollama,mock,factory}.py` | 工厂模式 + 失败回退到 mock |
| **爬虫框架** | `apps/api/app/services/scrapers/{base,registry,utils}.py` + `scrapers/*.py` | 3 个真实源 + 自动发现 |
| **加密 / 密钥** | `apps/api/app/core/secret.py:1` | Fernet；`BIZ_BP_AI_SECRET_KEY` 控制 |
| **前端根布局** | `apps/web/app/layout.tsx:1` | antd registry + ConfigProvider |
| **前端登录守卫** | `apps/web/middleware.ts:1` | Edge runtime；cookie 缺失 → `/login` |
| **BFF 代理模式** | `apps/web/app/api/**/route.ts` | 转发 `cookie` + `content-type` + body；`export const dynamic = "force-dynamic"` |
| **共享类型** | `packages/types/src/index.ts:1` | 与 `apps/api/app/core/registry.py` 的 Pydantic schema 同步 |
| **UI 组件** | `packages/ui/src/{UniversalKpiCard,UniversalChart,UniversalAgGrid}.tsx` | 通用，0 业务线硬编码 |

更细的架构图见 [`docs/architecture-overview.md`](docs/architecture-overview.md)。

---

## 6. 常见操作

| 我想... | 去哪 |
|---|---|
| 启动 / 重启 / 重置 dev 服务 | [`docs/maintenance/operations.md`](docs/maintenance/operations.md) §1 |
| 看 API / Web / pgserver 的日志 | [`docs/maintenance/operations.md`](docs/maintenance/operations.md) §2 |
| 手动运行某个爬虫 | [`docs/maintenance/operations.md`](docs/maintenance/operations.md) §3 |
| 加一个管理员账号 | [`docs/maintenance/operations.md`](docs/maintenance/operations.md) §4 |
| 轮换 `JWT_SECRET` / `BIZ_BP_AI_SECRET_KEY` / `BIZ_BP_SERVICE_TOKEN` | [`docs/maintenance/operations.md`](docs/maintenance/operations.md) §5-7 |
| 重置 dev DB | [`docs/maintenance/operations.md`](docs/maintenance/operations.md) §1.3 |
| 查 / 备份 / 还原 Postgres | [`docs/maintenance/operations.md`](docs/maintenance/operations.md) §8-9 |
| 跑 pytest | `cd apps\api && python -m pytest -q` |
| TypeScript 类型检查 | `cd apps\web && npx tsc --noEmit` |
| 端到端冒烟 | `docs/e2e-verification.md` |

---

## 7. 扩展系统

| 我想新增一个... | 去哪 |
|---|---|
| **业务线**（10 → 11） | [`docs/maintenance/extending.md`](docs/maintenance/extending.md) §1（5 步复制-修改） |
| **LLM 模型** | [`docs/maintenance/extending.md`](docs/maintenance/extending.md) §2（管理 UI 一行 POST 即可） |
| **BFF 代理** | [`docs/maintenance/extending.md`](docs/maintenance/extending.md) §3（参考 `[[...path]]/route.ts` 模式） |
| **告警规则** | [`docs/maintenance/extending.md`](docs/maintenance/extending.md) §4（编辑 `business_lines/<line>/alerts.yaml`） |
| **API 端点** | [`docs/maintenance/extending.md`](docs/maintenance/extending.md) §5（路由 + Pydantic schema + 角色守卫） |
| **Pydantic schema** | [`docs/maintenance/extending.md`](docs/maintenance/extending.md) §6 |
| **共享 TypeScript 类型** | [`packages/types/README.md`](packages/types/README.md) |

**核心约束**：`apps/` 与 `packages/` 中**绝不** `import` `business_lines/*`。
唯一允许的"业务线 → 核心"接口是 `business_lines/<line>/api/router.py`（被 `registry.py` 通过 importlib 加载）。
这条边界由 `docs/architecture-audit-2026-09-03.md` 中的 10/11 PASS 审计保证。

---

## 8. 故障排查

参见 [`docs/maintenance/troubleshooting.md`](docs/maintenance/troubleshooting.md)。常见症状：

- "AI Model 404 from DeepSeek" → `apps/api/app/routers/ai_models.py` + `apps/api/app/core/secret.py`
- "Copilot 503 upstream" → `pgserver` 挂了，重启
- "Audit 中间件静默失败" → DB 连接池陈旧；`audit.py` 会自动重试一次
- "BFF 401" → 检查是否转发 `cookie` 头（参见 `apps/web/app/api/**/route.ts`）
- "401 on BFF calls in production" → 跨主机部署时浏览器不再带第三方 cookie；必须走 BFF
- "STALE: `git status` shows `biz-bp-portal` as untracked" → 这是 reparse-point 假象（见 [§10.1](#101-仓库的-reparse-point-假象)）

---

## 9. 约定（必读）

### 9.1 环境变量前缀

- **Python 端**：所有 `apps/api/` 配置通过 `BIZ_BP_*` 前缀（参见 `apps/api/app/core/config.py:11`）
  - 例外：`JWT_SECRET`（不带前缀，跟业界惯例一致）
- **TypeScript 端**：`NEXT_PUBLIC_*` 会被打包到前端 bundle；其它 `BIZ_BP_*` 仅在 Node 服务端可见
- **历史重命名**：`FIN_BP_*` → `BIZ_BP_*` 于 2026-09-03 完成（commit `3442b3f`）。**新代码禁止使用 `FIN_BP_` 前缀**。

### 9.2 BFF 代理模式

所有浏览器 → 后端的请求必须**经过** `apps/web/app/api/**/route.ts`。
两条规则：
1. **必须**转发 `cookie` 头（否则 RBAC 失效，401）
2. **必须** `export const dynamic = "force-dynamic"`（避免 Next 14 静态化）

模板见 `apps/web/app/api/ai-models/[[...path]]/route.ts:1`。

### 9.3 审计中间件

`apps/api/app/middleware/audit.py:1` 在**每个**已认证请求里写一行 `raw.audit_log`。
它**绝不**阻塞响应（写入是后台 asyncio task），但**DB 写入失败时会重试一次**（参见 [§10.3](#103-审计中间件重试一次的-rationale)）。

### 9.4 业务线代码

- `business_lines/<line>/` 是**唯一**可以包含业务线特定代码的位置
- 文件名 8 标配：`manifest.yaml` / `indicators.yaml` / `sensitivity.yaml` / `forecast.yaml` / `alerts.yaml` / `api/router.py` / `dbt/models/*.sql` / `data/seed/*`（4-5 个 + 可选 web/pages）
- 模板在 `business_lines/_template/`，每个 `.example` 文件复制后**重命名去掉 `.example`**

完整规范见 [`docs/maintenance/conventions.md`](docs/maintenance/conventions.md)。

---

## 10. 已知陷阱

### 10.1 仓库的 reparse-point 假象

`C:\Users\mozzi\.mavis\workspace\biz-bp-portal` 是一个 **Windows reparse-point**（符号链接），
真实路径在 `C:\Users\mozzi\.minimax\workspace\biz-bp-portal`。
Git **不会**提交 reparse-point 本身，所以**永远不要**：
- `mv` 仓库根目录
- `Remove-Item` `C:\Users\mozzi\.mavis\workspace\biz-bp-portal`（会破坏链接）

如果 git 在 `C:\Users\mozzi\.mavis\workspace\biz-bp-portal` 提示
"untracked file `biz-bp-portal`"（即一个名为自己的目录），说明 reparse-point 解析失败。
修复：关闭该会话，在 `C:\Users\mozzi\.minimax\workspace\biz-bp-portal` 直接工作。

参见 commit `cf2d8f1`（chore: 修复 reparse-point 解析的工具链 workaround）。

### 10.2 PowerShell 编码陷阱

PowerShell 5.1 默认 GBK 编码。读 / 写 CJK 文件时：
- **必须** `Get-Content -Encoding UTF8` / `Set-Content -Encoding UTF8`
- **推荐** 用 Python 替代：`py -X utf8 -c "..."`
- **永远不要** `Get-Content | Set-Content` 的 pipeline（会破坏 CJK 注释 / 字符串）

详细 Windows 限制见环境提示（`<windows-behavior>` 块）。

### 10.3 审计中间件"重试一次"的 rationale

参见 `apps/api/app/middleware/audit.py:140-175`：
1. asyncpg 缓存连接池可能在 `pgserver` 重启后持有"已关闭的连接"
2. 第一次写失败 → 调 `_db_session.reset_engine()` 丢弃旧池
3. 用全新池重试一次
4. 二次失败仅 `WARNING` log，**不抛异常**——审计是 sidecar，不是 gate

所以：API 日志里偶现 `audit_log write failed ... attempt 1/2, will reset engine and retry` 是**正常**的。
如果持续失败超过 5 分钟，说明 Postgres 整体不可用，应用层会先报错（不是审计）。

### 10.4 "2 个 always-stale background tasks" 假象

Mavis 系统偶尔会在 `<system-reminder>` 里注入"background task 还活着"的提示。
如果这些 task 是来自**当前 session 之前**的工作（比如先前的爬虫 run、API 重启），
**直接忽略**，不要 action 它们。

### 10.5 不要 `Remove-Item` 删除代码

Windows 上 `Remove-Item` 在 reparse-point 路径下会**直接破坏 symlink**。
**唯一安全**的删除方式：

```powershell
py -X utf8 -c "import os; os.remove(r'C:\path\to\file.py')"
```

或者：

```powershell
# 调用 mavis-trash（如果存在）
mavis-trash C:\path\to\file.py
```

### 10.6 `gh push warns "repository moved"`

HTTPS 的 GitHub URL 偶尔会大小写重定向（`Njryadmin/biz-bp-portal` vs `njryadmin/biz-bp-portal`）。
第一次 push 警告**无害**；后续 push 会自动用正确 URL。

### 10.7 不要重命名 `finbp_token` cookie

浏览器里所有活跃会话的 `finbp_token` cookie 都会失效——所有用户需要重新登录。
`BIZ_BP_COOKIE_NAME` 可以改，但**生产环境改完必须广播"请重新登录"通告**。

### 10.8 不要重命名 `biz-bp-portal` 目录

如 §10.1 所述，reparse-point 锚定了这个名字。

---

## 11. 不要动的代码（load-bearing）

| 区域 | 为什么不能动 |
|---|---|
| `apps/api/app/routers/registry.py:44-66`（`importlib` loader） | 业务线自动发现的唯一入口；任何硬编码都破坏通用性 |
| `apps/api/app/middleware/audit.py:140-175`（重试一次） | DB-down 时的最后防线；去掉会导致响应被阻塞 |
| `apps/api/app/db/bootstrap.py:39-80`（DDL 列表） | 所有 DDL 都是 `IF NOT EXISTS`，可以加；不能从中间删 |
| `apps/web/middleware.ts:43-61`（cookie 守卫） | Edge runtime；改前先验证 RSC 兼容 |
| `apps/web/app/api/**/route.ts` 中 `cookie` header 转发 | 去掉就 401；这是 BFF 的全部价值 |
| `business_lines/registry.yaml` 中已注册的 9 条业务线 | 改 id 会让生产 cookie 中的 `bp:<line>` 角色全部失效 |
| `.pgdata/`（嵌入式 pgserver 的数据目录） | 删掉等于 `pg_reset`；用 `pgserver_runner.py --reset` 而不是手删 |
| `apps/api/app/fin_bp_api.egg-info/` | setuptools 自动生成；删除会触发重新构建 |

---

## 12. 文档地图

| 主题 | 文件 |
|---|---|
| 5 分钟上手 (v2) | [`README.md`](README.md) |
| 生产部署 (含 iStoreOS 端口偏移) | [`DEPLOY.md`](DEPLOY.md) |
| 维护手册（你正在读） | [`MAINTENANCE.md`](MAINTENANCE.md) |
| AI 代理交接 (v2) | [`AGENTS.md`](AGENTS.md) |
| 架构图（5 张） | [`docs/architecture-overview.md`](docs/architecture-overview.md) |
| 架构审计 | [`docs/architecture-audit-2026-09-03.md`](docs/architecture-audit-2026-09-03.md) |
| **v2 RBAC + Manifest v2 + Admin + Dashboard + Cross-line 全量交付** | [`docs/v2-rbac-deliverable.md`](docs/v2-rbac-deliverable.md) |
| **v2 多租户 M1-M3** | [`docs/multi-tenant-deliverable.md`](docs/multi-tenant-deliverable.md) |
| **v2 Dashboard (E 任务)** | [`docs/dashboard-deliverable.md`](docs/dashboard-deliverable.md) |
| **v2 跨业务线汇总 (G 任务)** | [`docs/cross-line-summary-deliverable.md`](docs/cross-line-summary-deliverable.md) |
| **v2 Migration Runner (F 任务)** | [`docs/migration-runner-deliverable.md`](docs/migration-runner-deliverable.md) |
| **v2 Admin 业务线编辑器 (D1+D2)** | [`docs/admin-business-line-deliverable.md`](docs/admin-business-line-deliverable.md) |
| v1 RBAC (superseded, 保留历史) | [`docs/rbac-2026-09-03-deliverable.md`](docs/rbac-2026-09-03-deliverable.md) |
| AI 模型注册表 | [`docs/ai-models-deliverable.md`](docs/ai-models-deliverable.md) |
| 用户管理 | [`docs/admin-users-deliverable.md`](docs/admin-users-deliverable.md) |
| 爬虫框架 | [`docs/scrapers-deliverable.md`](docs/scrapers-deliverable.md) |
| 5 步新增业务线（旧，可作补遗） | [`docs/plugin-howto.md`](docs/plugin-howto.md) |
| 维护手册子目录 | [`docs/maintenance/`](docs/maintenance/) |
| 完整文档索引 | [`docs/README.md`](docs/README.md) |
| 变更日志 (v2 在顶部) | [`docs/changelog.md`](docs/changelog.md) |

---

## 13. 联系 & 升级

- **问题报告**：GitHub Issues（私有仓）
- **Code review**：所有 PR 必须有 1 个 reviewer 签字
- **变更规范**：commit message 用 `feat(...)` / `fix(...)` / `chore(...)` / `docs(...)` 前缀
- **v2 升级路径**：从 v0.1.0 (4 角色) → v2.0.0 (8 角色) **自动 backfill**（`infra/migrations/001_rbac_v2.sql`），**业务无感**。详细见 [`docs/v2-rbac-deliverable.md`](docs/v2-rbac-deliverable.md) §14。
- **多租户升级**：v0.1.0 数据自动 backfill 到 default tenant (UUID `00000000-...`)，**0 数据丢失**。详细见 [`docs/multi-tenant-deliverable.md`](docs/multi-tenant-deliverable.md) §8。
