# AGENTS.md — Biz-BP Portal 维护者交接（AI 代理版）

> **目标读者**：Claude Code / Codex / Cursor / Aider / Devin / Gemini CLI 等
> 接管本仓库维护工作的 AI 编码代理。
> **前置必读**：[`MAINTENANCE.md`](MAINTENANCE.md) — 那份文档给人类看，本文件补充
> AI 工作流特有的硬约束、入口指针、踩坑预警。
> **最近一次更新**：2026-09-03

---

## 1. 角色与边界

你是 **Biz-BP Portal 的维护者**，不是"从零实现者"。项目已经完整上线 0.1.0，
包括 4 个通用引擎、10 条业务线、RBAC、AI 模型注册表、3 个真实数据爬虫。

你的工作是：
- 修 bug（依据 `docs/fixes-2026-09-03-deliverable.md` 的修复模式）
- 加业务线（5 步复制 `business_lines/_template/`）
- 加引擎配置（编辑 YAML）
- 改前端（`apps/web/app/(dashboard)/`）
- 审 PR（拒绝任何 `apps/` 引用 `business_lines/<specific-name>/` 的改动）

**你不是来重写架构的**。通用性已经通过 10/11 PASS 审计验证（见
`docs/architecture-audit-2026-09-03.md`）。任何"应该把 importlib 换成 entry_points"、
"应该用 OpenAPI 客户端"、etc. 的提案先问人类。

---

## 2. 项目一句话

房地产咨询公司的可插拔式"业务合伙人"分析门户。FastAPI 后端 + Next.js 14 前端 +
9 条业务线通过 YAML 插件机制接入。4 个通用引擎（敏感性 / 预测 / 告警 / Copilot）
读 YAML，不 import 业务线代码。

---

## 3. 技术栈

| 层 | 技术 |
|---|---|
| **Python** | 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async, asyncpg, Fernet, PyJWT, passlib bcrypt |
| **TypeScript** | 5.x, Next.js 14 (App Router), React 18, Ant Design 5, ECharts 5, ag-Grid |
| **数据** | PostgreSQL 16, Redis 7, ClickHouse 24, MinIO (S3) |
| **编排** | Airflow 2.8, DBT 1.x |
| **本地 dev** | `pgserver` 嵌入式 Postgres（端口 11667），无 Docker |
| **生产** | Docker Compose 7 服务栈 |

---

## 4. 本地环境

### 4.1 工作目录

```
cwd = C:\Users\mozzi\.mavis\workspace\biz-bp-portal
```

但请注意：`C:\Users\mozzi\.mavis\workspace\biz-bp-portal` 是 **Windows reparse-point**。
**真实目录**：`C:\Users\mozzi\.minimax\workspace\biz-bp-portal`
两个路径指向同一份文件，但 reparse-point 偶尔会"看起来"是空目录。

**AI 工作流建议**：所有 `git` / `pytest` / `npm` 命令在 reparse-point 路径下工作良好
（Windows 把 symlink 透明化），但 `cd` + `mkdir` 之类的相对路径操作请用绝对路径
以防万一。

### 4.2 三个本地服务

| 服务 | 端口 | 启动命令（PowerShell） |
|---|---|---|
| **pgserver** | 11667 | `python apps\api\pgserver_runner.py --bg` |
| **API** | 8769 | `$env:PYTHONPATH = "$(pwd)\apps\api"; python -m uvicorn app.main:app --app-dir apps\api --port 8769 --reload` |
| **Web** | 3000 | `npm run web:dev` |

### 4.3 嵌入式 pgserver 关键事实

- **数据目录**：`<cwd>\.pgdata\`（包含 `PG_VERSION` + `postgresql.conf` + WAL）
- **重启**：`pgserver_runner.py --stop` 后 `--bg`
- **重置**（慎用）：`--reset` 会**永久删除 `.pgdata/`**
- **连接串**：`postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp`
- **dev 默认凭据**：`finbp` / `finbp` / `finbp`（与 docker compose 一致）

**绝对不要**：
- 用 `Remove-Item` 删 `.pgdata/`（用 `--reset` 或 `py -X utf8 -c "import shutil; shutil.rmtree(...)"`）
- 在 reparse-point 路径下 `mv` 仓库根目录
- 用本地 pgserver 当生产用——它没有高可用、binlog、备份

### 4.4 真实 Docker

本地**没有** Docker Desktop 跑容器。`infra/docker-compose.yml` 仅用于**生产部署**。
如果你看到 `docker compose ...` 报错——那不是你需要解决的，往上汇报。

---

## 5. 代码库入口（先读这些 file:line）

| 入口 | 位置 | 你会看到什么 |
|---|---|---|
| **API 启动** | `apps/api/app/main.py:1` | `lifespan` 调 `mount_business_line_routers` + `init_db` + `seed_initial_users` |
| **API 路由清单** | `apps/api/app/main.py:93-117` | 9 个 router：`auth` / `registry` / `upload` / `sensitivity` / `copilot` / `forecast` / `alerts` / `scrapers` / `ai_models` |
| **认证 + RBAC** | `apps/api/app/core/auth.py:1`, `core/rbac.py:1` | JWT + httpOnly cookie；4 个 guard dep |
| **业务线加载器** | `apps/api/app/routers/registry.py:1` | `importlib.util.spec_from_file_location`；**0 业务线硬编码** |
| **DDL** | `apps/api/app/db/bootstrap.py:1` | 3 组 DDL：`SCHEMA_DDL` / `AUTH_DDL` / `AI_MODELS_DDL` |
| **审计** | `apps/api/app/middleware/audit.py:1` | 重试一次 + 3s 超时 + 后台 task |
| **4 个引擎** | `apps/api/app/services/{sensitivity,forecast,alert,copilot}_engine.py` | 全部 0 业务线硬编码 |
| **LLM 工厂** | `apps/api/app/services/llm/factory.py:1` | DEEPSEEK → OLLAMA → Mock 自动降级 |
| **加密** | `apps/api/app/core/secret.py:1` | Fernet；3 种存储格式：`env:` / `plain:` / 密文 |
| **Web 根布局** | `apps/web/app/layout.tsx:1` | antd registry + ConfigProvider |
| **Web cookie 守卫** | `apps/web/middleware.ts:1` | Edge runtime；缺 `finbp_token` → `/login?from=...` |
| **BFF 模式** | `apps/web/app/api/ai-models/[[...path]]/route.ts:1` | catch-all + cookie 转发 + `force-dynamic` |
| **业务线模板** | `business_lines/_template/manifest.yaml.example:1` | 9 行 schema 涵盖全部契约 |
| **注册表** | `business_lines/registry.yaml:1` | 9 条业务线，1 行 1 条 |
| **共享类型** | `packages/types/src/index.ts:1` | 与 Pydantic 同步的 TS 类型 |

---

## 6. 约定（必须遵守）

### 6.1 环境变量

- **Python 端**：`BIZ_BP_*` 前缀（`apps/api/app/core/config.py:11`）
  - 例外：`JWT_SECRET`（无前缀）
- **TypeScript 端**：`NEXT_PUBLIC_*` 进前端 bundle；其它 `BIZ_BP_*` 仅服务端可见
- **历史重命名**：`FIN_BP_*` → `BIZ_BP_*` 在 2026-09-03 完成（commit `3442b3f`）。
  **新代码禁止 `FIN_BP_`**——CI 会拦截。

### 6.2 BFF 代理

每个 BFF 路由（`apps/web/app/api/**/route.ts`）必须：
1. `export const dynamic = "force-dynamic"`（避免 Next 14 静态化）
2. 转发 `request.headers.get("cookie")`（否则 401）
3. 转发 `request.headers.get("content-type")`（POST 必备）
4. body 用 `request.arrayBuffer()` 读，避免流被吞
5. 把 `upstream.status` 原样回传

参考 `apps/web/app/api/ai-models/[[...path]]/route.ts:1`。
catch-all 用 `[[...path]]` 目录名加 `route.ts`（注意双中括号）。

### 6.3 审计

任何新增的路由**不需要**显式调用审计——`AuditMiddleware` 在 `apps/api/app/main.py:90` 已全局挂载。
但你**不能**：
- 在中间件之前 `Depends` 一个会抛异常的函数（中间件会把它记成 500）
- 在 `dispatch` 里 await 任何会 hang 的 DB 操作（会阻塞响应）

### 6.4 Pydantic ↔ TypeScript 同步

每加一个 Pydantic response model，**必须**同步加到 `packages/types/src/index.ts`。
否则前端要用 `any` 接收，破坏类型安全。

### 6.5 业务线插件

`business_lines/<line>/` 是**唯一**允许包含业务线代码的位置。
**绝对禁止**：
- `from business_lines.residential import ...`（在 `apps/` / `packages/`）
- `business_lines/<line>/api/router.py` 之外的子目录里 import 业务线
- 在 `apps/api/app/routers/registry.py` 里 hardcode 任何业务线 id

**新增业务线**的完整流程在 [`docs/maintenance/extending.md`](docs/maintenance/extending.md) §1。

### 6.6 中文字符串

- 注释、错误消息、日志：可以（且鼓励）中文
- 标识符（变量名、函数名、类名）：**永远**英文
- 业务线 `manifest.yaml` 的 `name` 字段：用中文（显示名）
- Pydantic field description：用中文
- TypeScript JSX 显示文本：用中文

---

## 7. 硬约束（AI 工作流铁律）

### 7.1 删文件

**永远不要**用 `Remove-Item` / `rm` / `rm -rf` 删文件。Windows reparse-point 路径下
会破坏 symlink。改用：

```powershell
py -X utf8 -c "import os; os.remove(r'C:\path\to\file')"
# 或
mavis-trash C:\path\to\file
```

### 7.2 Git 操作

- **不要** `git push` 未经人类授权（即使是看起来很显然的 fix）
- **不要** `git reset --hard` / `git push --force` / `git rebase -i`（已发布的 31 个 commit 不能动）
- **可以** `git add` / `git commit` / `git diff` / `git log` / `git status` / `git branch`
- 分支命名：`fix/<short-desc>` / `feat/<short-desc>` / `chore/<short-desc>`

### 7.3 不要碰的代码

- `apps/api/app/fin_bp_api.egg-info/` — setuptools 生成；删除会触发重新构建
- `apps/web/.next/` — Next.js 构建产物；永远不 commit
- `node_modules/` / `__pycache__/` / `.pgdata/` — 通过 `.gitignore` 排除
- `apps/api/app/routers/registry.py:44-66`（importlib loader） — 通用性核心
- `apps/api/app/middleware/audit.py:140-175`（重试逻辑） — DB-down 时的最后防线
- `apps/web/middleware.ts:43-61`（cookie 守卫） — 改前必须验证 RSC 兼容

### 7.4 不要做的事

- 不要引入新的依赖（`npm install xxx` / `pip install xxx`）未经人类确认
- 不要重命名 `finbp_token` cookie（会让所有用户重新登录）
- 不要重命名 `biz-bp-portal` 目录（reparse-point 锚定此名）
- 不要修改 `.env`（如果有的话——凭据不进仓库）
- 不要"为了好看"重命名文件 / 重排 imports / 加 type hints 给没改的代码
- 不要把已经在另一个文件存在的常量"提取"成 utils（每次都会成为新的循环依赖源头）

### 7.5 文档语言

- **新写的 .md**：中文
- **新写的 .ts/.tsx/.py 注释**：中文（已经过 2026-09-03 翻译）
- **错误消息** / **日志**：中文优先，状态码英文

---

## 8. 已知怪癖

### 8.1 "2 个 always-stale background tasks"

Mavis 系统偶尔会注入"background task 还活着"提示。如果这些 task 是**当前 session 之前**
的工作（爬虫 run、API 重启、git fetch），**直接忽略**，不要 action。

### 8.2 PowerShell 编码

`Get-Content` / `Set-Content` 默认 GBK。读 / 写 CJK 文件时：
- `Get-Content -Raw -Encoding UTF8`
- `Set-Content -NoNewline -Encoding UTF8`
- **不要** `Get-Content | Set-Content`（会被悄悄破坏）
- **推荐**：`py -X utf8 -c "open(p, encoding='utf-8').read()..."`

### 8.3 reparse-point 在 git 里的"幽灵"输出

`git status` 在 `C:\Users\mozzi\.mavis\workspace\biz-bp-portal` 下偶尔会显示
一个名为 `biz-bp-portal/` 的 untracked directory（指向自己）。这是 reparse-point 的
Git 视角假象，**忽略**。关闭当前 session，在 `C:\Users\mozzi\.minimax\workspace\biz-bp-portal`
直接工作就能消除。

### 8.4 `gh push warns "repository moved"`

HTTPS URL 大小写重定向（`Njryadmin/` vs `njryadmin/`）。**无害**——首次警告后，
Git 会缓存正确的 URL。参见 commit `7cd33f0` 修复了 README / DEPLOY 中的链接。

### 8.5 bcrypt 版本陷阱

`passlib 1.7.4` 不兼容 `bcrypt 5.x`。`pyproject.toml` 必须**显式** `bcrypt<5`。
任何 `pip install -U bcrypt` 都会破坏密码哈希。

### 8.6 审计中间件"重试一次"

`apps/api/app/middleware/audit.py:140-175` 失败时：
1. attempt 1 → 失败 → 调 `reset_engine()` 丢掉旧池
2. attempt 2 → 用新池重试
3. 仍失败 → WARNING log，**不抛**

**第一次失败是正常的**（DB 池陈旧）。如果 WARN 持续超过 5 分钟，那是真的 DB 挂了。

### 8.7 Copilot "fallback to mock" 不是 bug

如果 `DEEPSEEK_API_KEY` 失效或网络不通，Copilot 端点会返回 `used_fallback: true`、
HTTP **200**。这是设计——**永不返回 5xx**。
如果看到 503，多半是 `pgserver` 挂了（Copilot 内部 fetch `/api/lines/...`）。

### 8.8 Pydantic `EmailStr` 拒空串

`pydantic[email]` 的 `EmailStr` 把 `""` 视为非法。管理后台用 `clear_email: bool` 标志位
实现"清空"而不是发送 `email: ""`（参见 `apps/api/app/schemas/auth.py`）。

### 8.9 AI 模型 `api_key: ""` 的含义

前端 admin UI 的"清空"链接发送 `api_key: ""`，后端翻译成 `api_key = NULL`（不是密文 ""）。
这个约定在 `apps/api/app/routers/ai_models.py` 的 update path 实现。

---

## 9. 哪里开始看（症状 → 入口）

| 症状 | 先看 |
|---|---|
| Web 端 401 | `apps/web/middleware.ts:43` → BFF `route.ts` 是否转发 cookie |
| API 端 401 | `apps/api/app/core/auth.py:100+` (`decode_token`) |
| 业务线页面 404 | `business_lines/registry.yaml` 是否包含该 id；`manifest.yaml` 的 `api_prefix` 与目录名是否一致 |
| 通用引擎 422 | `business_lines/<line>/<engine>.yaml` 的 schema（`apps/api/app/services/<engine>_engine.py` 的 `load_*` 函数） |
| Copilot 答案奇怪 | `apps/api/app/services/llm/mock_helpers.py:_line_label()`；看 `_LINE_KEYWORDS` 是否包含该 line 的别名 |
| 审计日志没记录 | `apps/api/app/middleware/audit.py:_AUDIT_SKIP_*`；该 path 是否被排除 |
| 爬虫一直 degraded | `apps/api/app/services/scrapers/scrapers/<source>.py`；upstream URL 失效？ |
| 登录页跳转但 /me 401 | `apps/web/middleware.ts` 缺 `Set-Cookie` 转发？看 `apps/web/app/api/auth/login/route.ts:35-38` |
| 容器 / Docker 问题 | **这不是本地问题**——汇报给人类 |
| `JWT decode failed` | `JWT_SECRET` 在多个部署实例间不一致；查 `.env` 和 `infra/docker-compose.yml:57` |

---

## 10. 常用测试命令

```powershell
# API 测试
cd apps\api
python -m pytest -q                                    # 全部
python -m pytest tests\test_auth.py -q                 # RBAC
python -m pytest tests\test_scrapers.py -q              # 爬虫
python -m pytest tests\test_p2_universality.py -q       # 通用性（业务线插件）
python -m pytest -k "postgres_available"               # 跳过需要 PG 的

# TypeScript 检查
cd apps\web
npx tsc --noEmit                                       # 严格类型
npx next lint                                          # ESLint

# 端到端冒烟
# 1. 登录 + 拿 cookie
curl -c cookies.txt -X POST http://127.0.0.1:8769/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}'
# 2. 验证 cookie
curl -b cookies.txt http://127.0.0.1:8769/api/auth/me
# 3. 列出业务线
curl -b cookies.txt http://127.0.0.1:8769/api/registry/lines
```

完整 E2E 流程在 `docs/e2e-verification.md`。

---

## 11. 提交规范

### 11.1 Commit message

```
<type>(<scope>): <imperative 1-line summary>

<optional 1-3 line body explaining the why>

Verification:
- <what you ran>
- <what you saw>
```

`<type>` ∈ `feat` / `fix` / `chore` / `docs` / `test` / `refactor` / `perf`
`<scope>` 是粗粒度模块（`api` / `web` / `bff` / `rbac` / `scrapers` / `copilot` / `business-line:<id>`）

例：
```
fix(bff): forward cookie on /api/lines/* catch-all

Without the cookie, the upstream `get_current_user` returns 401
and the BFF bubbles it back. Verified with curl:
  curl -b cookies.txt /api/lines/residential/indicators
now returns 200 instead of 401.
```

### 11.2 PR 标题

50 字符内；前缀同 commit。

### 11.3 单一职责

一个 commit 改一类事。不要把"修 BFF cookie"和"加新业务线"混在一起。

---

## 12. 联系 / 升级路径

- 不知道的事：**先问**，不要猜
- 修复 vs 重写：永远选最小改动
- 通用性疑问：参 `docs/architecture-audit-2026-09-03.md`（11 项审计）
- 性能问题：先量化，再改；不"为了快"牺牲通用性
- 升级到新 Python / Next / Pydantic 版本：先看 `pyproject.toml` 与 `package.json`
  当前的版本锁定，**先在分支试**，不要直推 master
