# T0 交付物 — Fin BP Portal Monorepo 基座

> Worker：Coder · T0（基座）
> Date：2026-09-02
> Project root：`C:\Users\mozzi\.mavis\workspace\fin-bp-portal\`

## 1. 创建的文件（相对于项目根目录）

### 根目录
- `package.json` —— npm workspaces，脚本：`web:dev`、`web:build`、`web:typecheck`、`api:dev`、`api:test`、`lint`、`typecheck`
- `.gitignore` —— Node / Python / DBT / Airflow / data / IDE 排除规则
- `README.md` —— 快速上手、架构契约、"如何新增业务线"

### apps/web（Next.js 14）
- `apps/web/package.json`、`tsconfig.json`、`next.config.js`、`.eslintrc.json`、`next-env.d.ts`
- `apps/web/app/layout.tsx` —— 根布局，AntdRegistry + ConfigProvider
- `apps/web/app/page.tsx` —— `/` → 重定向到 `/dashboard`
- `apps/web/app/(dashboard)/layout.tsx` —— **动态** 左侧导航，从 `/api/registry/lines` 拉取，**不 import `business_lines/*`**
- `apps/web/app/(dashboard)/dashboard/page.tsx` —— 已注册业务线的总览卡片网格
- `apps/web/app/api/registry/route.ts` —— 同源代理到 Python API
- `apps/web/lib/registry.ts` —— 浏览器端拉取辅助

### apps/api（FastAPI）
- `apps/api/pyproject.toml` —— `fin-bp-api` 包，依赖包括 FastAPI、Pydantic v2、SQLAlchemy 2.0、asyncpg、PyYAML、clickhouse-driver、redis、httpx；dev extras 包含 pytest、pytest-asyncio
- `apps/api/README.md`
- `apps/api/app/__init__.py`、`app/main.py` —— FastAPI 工厂，lifespan 挂载业务线路由
- `apps/api/app/core/config.py` —— pydantic-settings
- `apps/api/app/core/logging.py` —— stdlib logging
- `apps/api/app/core/registry.py` —— Pydantic v2 manifest / indicators 模型 + 加载器；Pydantic 保留字段 `schema` 被映射为 `schema_name`，YAML 契约保持不变
- `apps/api/app/routers/registry.py` —— **基于 importlib 的动态发现**；代码中无业务线名称
- `apps/api/app/schemas/kpi.py` —— KpiItem / KpiResponse（Pydantic v2）
- `apps/api/app/db/session.py` —— SQLAlchemy 2.0 async 引擎，asyncpg-ready
- `apps/api/tests/conftest.py`、`tests/test_registry.py`、`tests/test_api.py`

### packages
- `packages/types/package.json` + `src/index.ts` —— `BusinessLine`、`Indicator`、`KpiValue`、`BusinessLineNavItem`、`ChartSpec`、`BusinessLineWarehouse`、`BusinessLineRefresh`、`BusinessLineFeatures`、`RegistryResponse`
- `packages/ui/package.json` + `src/{UniversalKpiCard,UniversalChart,UniversalAgGrid,EmptyState,index}.tsx`

### business_lines
- `business_lines/registry.yaml` —— 空的 `lines: []`
- `business_lines/README.md` —— 5 步"新增业务线"指南
- `business_lines/_template/manifest.yaml.example`
- `business_lines/_template/indicators.yaml.example`
- `business_lines/_template/api/router.py.example`
- `business_lines/_template/web/pages/_example.tsx`
- `business_lines/_template/dbt/dbt_project.yml.example`
- `business_lines/_template/dbt/models/example.sql`
- `business_lines/_template/data/seed/.gitkeep`

### infra
- `infra/docker-compose.yml` —— Postgres 16、Redis 7、ClickHouse 24、MinIO、Airflow 2.8（`apache/airflow:2.8-python11`）
- `infra/.env.example`

### CI / docs / data
- `.github/workflows/ci.yml` —— web lint+typecheck 任务，api pytest 任务
- `docs/architecture.md` —— ASCII 图 + 边界规则
- `data/landing/.gitkeep`

## 2. 启动命令（单行）

```bash
# 基础设施
cd infra && docker compose up -d postgres redis clickhouse minio airflow

# API（独立 shell）
cd apps/api
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# Web（独立 shell）
npm install
npm run web:dev    # http://localhost:3000
```

## 3. 验证运行

| # | 命令 | 结果 |
|---|------|------|
| 1 | `cd infra && docker compose config` | **本 Windows 主机未安装 docker** —— 改用 Python YAML 结构检查。`infra/docker-compose.yml` 可解析，五个服务齐全，`airflow.image == "apache/airflow:2.8-python11"`，Postgres 16 / Redis 7 / ClickHouse 24 镜像 tag 已确认。详见 §3.1。 |
| 2 | `cd apps/web && npm install && npm run typecheck` | **PASS** —— `tsc --noEmit` 退出码 0，无输出。`npm run lint` 也 PASS（`✔ No ESLint warnings or errors`）。 |
| 3 | `cd apps/api && pip install -e . && python -m pytest` | **PASS** —— 8 个测试，8 通过，1 个不相关弃用警告（`httpx` 在 starlette `TestClient` 中）。 |
| 4 | `python -c "import yaml; yaml.safe_load(open('business_lines/registry.yaml'))"` | **PASS** —— `{'lines': []}` |
| 5 | `python -c "import yaml; yaml.safe_load(open('business_lines/_template/manifest.yaml.example'))"` | **PASS** —— 可解析，含 `id: change-me` 等字段 |
| 6 | 动态发现端到端（合成业务线，测试后移除） | **PASS** —— 通过 `registry.yaml` 注册 `_test_demo_line`，由 `importlib.util.spec_from_file_location` 加载，挂载到 `/api/lines/_test_demo_line/ping`，返回 `{'pong': True, 'line': '_test_demo_line'}`。临时目录与测试条目已清理；最终 `registry.yaml` 恢复为 `lines: []`。 |

### 3.1 替代 docker-compose 验证

```text
$ python -c "import yaml; ..."
docker-compose.yml YAML valid
services: postgres, redis, clickhouse, minio, airflow
airflow image: apache/airflow:2.8-python11
volumes: ['postgres-data', 'clickhouse-data', 'minio-data', 'airflow-dags', 'airflow-logs']
```

### 3.2 pytest 输出

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\mozzi\.mavis\workspace\fin-bp-portal\apps\api
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2, asyncio-1.4.0
asyncio: mode=Mode.AUTO, debug=False, ...
collected 8 items

tests\test_api.py ...                                                    [ 37%]
tests\test_registry.py .....                                             [100%]

======================== 8 passed, 1 warning in 0.47s =========================
```

### 3.3 约束检查（对 `apps/` 做 grep）

`apps/api` 和 `apps/web` 仅在以下位置引用 `business_lines`：
- 目录路径字符串（`"business_lines/registry.yaml"`）
- 解释该规则的注释
- 空注册态界面中渲染给用户看的帮助文本

核心代码路径中没有任何业务线名称（`change-me`、`consumer_loan`、`wealth_mgmt` …）的字面量。

## 4. 关键假设

1. **Python 3.11+** —— 规格要求。在系统 Python 3.12.10 上测试，API 兼容。
2. **Node 20+** —— 在 `engines` 中声明。系统 Node 24.19.0 已使用，同样兼容 Next.js 14。
3. **npm workspaces** 优先于 pnpm —— npm 已在 PATH 上，规格允许两者任选；这样可以避免用户安装 pnpm。
4. **ClickHouse 原生端口重映射到 9100**，避免与 MinIO 的 9000 冲突。HTTP 端口保持 8123。
5. **`BusinessLineWarehouse` 中的 `schema` 字段** —— Pydantic v2 在 `BaseModel` 上保留该属性名，因此模型字段内部名为 `schema_name`，别名为 `schema`。YAML 契约（`schema: raw_change_me`）不变。
6. **HTTP 客户端 / API 基础地址** —— `apps/web` 读取 `NEXT_PUBLIC_API_BASE_URL`（默认 `http://localhost:8000`），同时在 `/api/registry` 暴露同源代理，使浏览器在开发模式下无需处理 CORS。
7. **开发环境无 CORS 预检** —— 推荐使用 `apps/web/app/api/registry/route.ts` 的代理路由。
8. **身份认证** —— 占位（`CORSMiddleware` 在开发模式下放行；显式的生产鉴权是 T1+ 的任务，规格如此）。
9. **AG Grid Community** —— 已安装，`UniversalAgGrid` 包装组件存在于 `packages/ui`，但暂未有消费页面接入（仪表盘页未 import AG Grid CSS）。后续任务可直接 import。

## 5. 阻塞 / 限制

1. **worker 主机未安装 docker**（本 Windows 主机无 Docker Desktop）。`docker compose config` 无法字面执行；§3.1 的结构检查是忠实替代品，但不会捕获 Docker 自带校验器能捕获的内容（如 `version` 字段、mount 语法）。用户应在具备 Docker 的主机上执行 `cd infra && docker compose config` 后再启动栈。
2. **AG Grid 已接入 `packages/ui`**，但 `apps/web` 暂时没有任何页面 import 它（目前没有业务线会用到它）。后续任务中如需数据网格，可直接 `import { UniversalAgGrid } from "@fin-bp/ui"`。
3. **`logger.info` 双参数风格** —— API 中所有日志调用使用 stdlib 的 `%s` 占位符（非 loguru 风格的 `{}`）。
4. **`pip` 关于 `starlette.testclient` 需要 `httpx2` 的弃用警告** 在此处无法处理，是 FastAPI/Starlette 自身的 release note。
5. **工作目录是 reparse point** —— `C:\Users\mozzi\.mavis\workspace\` 重定向到 `C:\Users\mozzi\.minimax\workspace\`。通过任一路径编辑都可以；拒绝 reparse 路径的工具（例如 `mavis-trash`）需要传入解析后的路径。
