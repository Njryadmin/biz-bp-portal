# 部署指南

> **仓库地址**：<https://github.com/Njryadmin/biz-bp-portal>（private）
> **本地路径**：`C:\Users\mozzi\.mavis\workspace\biz-bp-portal`
> **远程 origin**：`https://github.com/Njryadmin/biz-bp-portal.git`
> **当前版本**：**v2.0.0** (InsightBP — 2026-09-04, 8 角色 RBAC + 多租户)

Biz-BP Portal 以 7 服务 Docker Compose 栈的形式交付。
本文档涵盖环境变量、部署拓扑和运维命令。
**v2 阶段新增**：iStoreOS 端口偏移 compose（`docker-compose.override.yml`）、Migration runner 端点、多租户首启注意事项。

## 0. 身份认证 / RBAC v2（2026-09-04）

所有 API 端点均由基于 JWT 的 RBAC v2 系统保护。**8 角色 + 5 数据域 + FIN/HR 物理隔离 + 多租户 M1-M3**。
首次启动时，API 会从业务线注册表自动创建账号（幂等——仅在 `users` 表为空时执行）。
完整设计参见
**[docs/v2-rbac-deliverable.md](docs/v2-rbac-deliverable.md)**（v2, 8 角色）。

**默认初始账号**（生产环境请通过 PATCH 或重建账号轮换密码）：

| 用户名 | 密码 | v2 角色 | 可见范围 / 域 |
|---|---|---|---|
| `admin` | `admin123` | `admin` + **`is_super_admin=TRUE`** (M2) | 全部 9 条业务线，全部 5 域**只读**，可切 tenant |
| `bp-residential` | `bp123456` | `line_owner:residential` (v1→v2 backfill) | 仅住宅，全部 5 域 |
| `bp-retail` | `bp123456` | `line_owner:retail` | 仅零售 |
| `bp-retail-leasing` | `bp123456` | `line_owner:retail-leasing` | 仅零售租赁 |
| `bp-valuation` | `bp123456` | `line_owner:valuation` | 仅估价 |
| `bp-advisory` | `bp123456` | `line_owner:advisory` | 仅顾问 |
| `bp-office-leasing` | `bp123456` | `line_owner:office-leasing` | 仅写字楼租赁 |
| `bp-investment` | `bp123456` | `line_owner:investment` | 仅投资 |
| `bp-project-management` | `bp123456` | `line_owner:project-management` | 仅项目管理 |
| `bp-industrial` | `bp123456` | `line_owner:industrial` | 仅工业地产 |

**v2 删除**：`bp-my-line`（被 v2 test_admin_v2_roles 自动 cleanup）。**新增 v2 角色用户名**（如需在测试 / 演示用）：

| 用户名 | 密码 | v2 角色 | 可见范围 / 域 |
|---|---|---|---|
| `finbp-residential` | `finbp123` | `fin_bp:residential` | 仅住宅，business/finance/project 读写，HR 域**不可见** |
| `hrbp-residential` | `hrbp123` | `hr_bp:residential` | 仅住宅，business/hr/client/project 读写，finance 域**不可见** |
| `finbp-global` | `finbp123` | `fin_bp_global` | 跨业务线 finance 读写，HR 域**不可见** |
| `hrbp-global` | `hrbp123` | `hr_bp_global` | 跨业务线 hr 读写，finance 域**不可见** |

**`is_super_admin` 概念**（v2 M2）：`admin` 用户在 `infra/migrations/004_tenant_m2_super_admin_and_triggers.sql` 自动标记 `is_super_admin = TRUE`。Super admin 可：
- 切 tenant via `X-Tenant-ID` header
- 跨租户查询（绕过 RLS via `app.bypass_rls = 'on'`）
- 创建/编辑 tenant（`POST /api/admin/tenants` / `PATCH /api/admin/tenants/{id}`）

**必需的环境变量**（添加到 `.env`）：

```bash
# 至少 32 个字符，使用以下命令生成：python -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET=change-me-in-production-32-chars-min

# 可选覆盖
JWT_ALGORITHM=HS256
JWT_EXPIRY_HOURS=24
BIZ_BP_BOOTSTRAP_ADMIN_USERNAME=admin
BIZ_BP_BOOTSTRAP_ADMIN_PASSWORD=admin123       # 生产环境请修改
BIZ_BP_COOKIE_SECURE=false                     # 生产环境设为 true（仅 HTTPS）
BIZ_BP_COOKIE_NAME=finbp_token
```

**冒烟测试**（`docker compose up -d` 之后）：

```bash
# 1. 未认证 → 401
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/registry/lines
# → 401

# 2. 以 admin 登录
curl -s -c /tmp/c.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
# → 200，cookie 写入 /tmp/c.txt

# 3. 带 cookie 访问注册表
curl -s -b /tmp/c.txt http://localhost:8000/api/registry/lines | jq '.lines | length'
# → 10

# 4. 以 bp-residential 登录
curl -s -c /tmp/c.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"bp-residential","password":"bp123456"}' >/dev/null
curl -s -b /tmp/c.txt http://localhost:8000/api/registry/lines | jq '.lines | length'
# → 1  （仅住宅）

# 5. 跨业务线访问被拒
curl -s -b /tmp/c.txt -o /dev/null -w "%{http_code}\n" \
  http://localhost:8000/api/lines/retail/indicators
# → 403
```

**审计日志**：每个已认证的请求都会记录到 `raw.audit_log`。以 admin 或
auditor 身份查询：

```bash
curl -s -b /tmp/c.txt 'http://localhost:8000/api/auth/audit-log?limit=10'
# 返回 { count, items: [{id, user_id, username, method, path, status_code, ...}, ...] }
```

保留策略：表会无限增长。生产环境请安排每日定时任务（例如通过 Airflow）
执行 `DELETE FROM raw.audit_log WHERE "timestamp" < NOW() - INTERVAL '90 days';`。

## 1. 部署拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│  浏览器 → http://localhost:3000                                │
└─────────────────┬───────────────────────────────────────────────┘
                  │ (Next.js SSR + BFF 代理 /api/* → :8000)
                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  web (Next.js 14, 生产模式)            :3000                   │
│  ├─ 读取 NEXT_PUBLIC_API_BASE_URL=http://api:8000              │
└─────────────────┬───────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  api (FastAPI + uvicorn)               :8000                    │
│  ├─ 4 个通用引擎（敏感性、Copilot、预测、告警）                  │
│  ├─ 3 个爬虫（NBS、链家、政策）                                │
│  ├─ 通过 importlib 动态发现业务线                              │
│  └─ BIZ_BP_API_BASE=http://api:8000（引擎自调用）              │
└─────────────────┬───────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  Airflow（scheduler + webserver）     :8080                      │
│  └─ 运行 ingest_daily + scrape_weekly DAG                      │
└─────────────────┬───────────────────────────────────────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
   Postgres           ClickHouse
    :5432              :8123/:9100
                  (MinIO :9000/:9001 文件存储)
                  (Redis :6379 缓存)
```

## 2. 环境变量

> 将 `.env.example` 复制为 `.env` 并填写真实值。
> api 服务从 compose 的 `environment:` 块读取变量（通过 `${VAR:-default}`
> 从 `.env` 插值）。web 服务则在**构建时**通过 `NEXT_PUBLIC_*` 接收变量
>（Next.js 约定）。

### 2.1 任何部署都必须配置

| 变量 | 范围 | 默认值 | 用途 |
|---|---|---|---|
| `BIZ_BP_PROJECT_ROOT` | api | `/app` | 容器内项目根路径 |
| `BIZ_BP_API_BASE` | api | `http://api:8000` | 引擎回调用 API 的地址 |
| `BIZ_BP_DATABASE_URL` | api | 由 PG 凭据组装 | asyncpg 连接串 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | compose | finbp/finbp/finbp | Postgres 凭据 |
| `NEXT_PUBLIC_API_BASE_URL` | web（构建时） | `http://api:8000` | BFF 代理转发目标 |

### 2.2 AI Copilot LLM（可选，但实际问答推荐配置）

| 变量 | 默认值 | 作用 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 空 | 设置后 Copilot 使用 DeepSeek V3 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | 可覆盖为其他 OpenAI 兼容服务 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名 |
| `DEEPSEEK_TIMEOUT` | `30` | 秒 |
| `DEEPSEEK_TEMPERATURE` | `0.3` | 采样温度 |
| `OLLAMA_BASE_URL` | 空 | 若设置（且 `DEEPSEEK_API_KEY` 为空），使用 Ollama |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Ollama 模型 |
| `OLLAMA_TIMEOUT` | `30` | 秒 |

**工厂优先级**：`DEEPSEEK_API_KEY` → `OLLAMA_BASE_URL` → MockBackend。
**自动回退**：如果 DeepSeek 返回 401/网络错误，响应会标记
`used_fallback: true` 并使用规则引擎答案（HTTP 200，绝不返回 500）。

### 2.3 Airflow / 数据集成

| 变量 | 默认值 | 用途 |
|---|---|---|
| `AIRFLOW__CORE__FERNET_KEY` | 随机 44 字符 base64 | Airflow 密钥加密 |
| `AIRFLOW_ADMIN_USERNAME` / `AIRFLOW_ADMIN_PASSWORD` | admin/admin | Webserver 登录 |
| `DBT_HOST` / `DBT_PORT` / `DBT_USER` / `DBT_PASSWORD` / `DBT_DBNAME` | 来自 PG | dbt profile 连接 |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | finbp / finbp12345 | S3 存储凭据 |

### 2.4 ClickHouse / Redis（可选，用于分析 + 缓存）

| 变量 | 默认值 |
|---|---|
| `CLICKHOUSE_DB` / `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` | finbp / finbp / finbp |
| `REDIS_HOST` / `REDIS_PORT` | redis / 6379 |

## 3. 部署命令

### 3.1 首次部署

```bash
git clone <repo-url> fin-bp-portal
cd fin-bp-portal

# 1. 配置密钥
cp .env.example .env
# 编辑 .env —— 至少在拥有密钥时设置 DEEPSEEK_API_KEY

# 2. 构建并启动
docker compose -f infra/docker-compose.yml --env-file .env up -d --build

# 3. 等待健康检查（api/web 等待 postgres）
docker compose -f infra/docker-compose.yml ps

# 4. 打开
open http://localhost:3000
```

### 3.2 常用运维命令

```bash
# 跟踪所有日志
docker compose -f infra/docker-compose.yml logs -f

# 仅跟踪应用日志
docker compose -f infra/docker-compose.yml logs -f api web

# 代码改动后仅重启 api
docker compose -f infra/docker-compose.yml restart api

# 重新构建并重启 api/web
docker compose -f infra/docker-compose.yml up -d --build api web

# 全部停止（数据卷保留）
docker compose -f infra/docker-compose.yml down

# 全部停止并清除数据卷
docker compose -f infra/docker-compose.yml down -v
```

### 3.3 健康检查

```bash
# API 健康
curl -fsS http://localhost:8000/api/registry/lines | jq

# Web 健康
curl -fsS http://localhost:3000/ -o /dev/null -w "%{http_code}\n"

# 数据库
docker compose -f infra/docker-compose.yml exec postgres pg_isready -U finbp

# Airflow
curl -fsS http://localhost:8080/health

# v2 关键端点
curl -fsS -b /tmp/c.txt http://localhost:8000/api/auth/me-v2 | jq   # v2 user
curl -fsS -b /tmp/c.txt http://localhost:8000/api/auth/me-tenant | jq  # v2 tenant
curl -fsS -b /tmp/c.txt http://localhost:8000/api/admin/migrations/status | jq  # v2 migration
```

## 4. iStoreOS 部署（端口偏移，v2 新增）

**适用场景**：iStoreOS / OpenWrt / 已有 moontv-core 等服务占用了标准端口（3000 / 8000 / 8080 等）。
主人硬件：**i3-N305 8 核 / 16GB RAM / iStoreOS 24.10.8 / Linux 6.6**。

### 4.1 已占用端口清单

| 服务 | 占端口 | 备注 |
|---|---|---|
| moontv-core | 3000 | 必须避让 |
| moontv-kvrocks | 6666 | 不冲突 |
| pdfcraft | 8050 | 不冲突 |
| stirling-pdf | 8444 | 不冲突 |
| OpenClaw gateway | 18789 | 不冲突 |
| OpenClaw web PTY | 18793 | 不冲突 |

### 4.2 端口偏移表（`infra/docker-compose.override.yml`）

| 服务 | 默认 | 偏移后 |
|---|---|---|
| Web | 3000 | **13000** |
| API | 8000 | **18000** |
| Airflow | 8080 | 18080 |
| MinIO S3 | 9000 | 19000 |
| MinIO console | 9001 | 19001 |
| Postgres | 5432 | 15432 |
| Redis | 6379 | 16379 |
| ClickHouse HTTP | 8123 | 18123 |
| ClickHouse native | 9100 | 19100 |

### 4.3 启动命令（iStoreOS）

```bash
# 用 -f 同时引用 override，**不**改原 docker-compose.yml
docker compose \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.override.yml \
  --env-file .env \
  up -d --build
```

**MVP 阶段关掉 ClickHouse + Airflow**（仅在 `profiles: ["full"]` 启用）：

```bash
# 默认 = 5 服务: postgres + redis + minio + api + web
docker compose \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.override.yml \
  --env-file .env \
  up -d --build

# 含 ClickHouse + Airflow (9 服务)
docker compose \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.override.yml \
  --env-file .env \
  --profile full \
  up -d --build
```

**入口 URL**（iStoreOS 偏移后）：

- Web：http://`<host>`:13000
- API：http://`<host>`:18000

**容器内 BFF 仍走原端口**（Docker network 内部）：`NEXT_PUBLIC_API_BASE_URL: http://api:8000` — 不需要因为外部端口偏移而改。

## 5. Migration runner（v2 F 任务）

`apps/api/app/db/migration_runner.py` 是 v2 自实现的轻量级 migration runner（**未**用 Alembic）。

### 5.1 自动应用（推荐）

`apps/api/app/main.py:1` 的 `lifespan` 在 API 启动时**自动跑**所有 pending migration。

```bash
# 启动 API → 自动跑 001 / 002 / 003 / 004
docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.yml up -d --build
```

### 5.2 手动跑（应急 / 排查）

```bash
# 手动跑 003
psql -U finbp -d finbp -f infra/migrations/003_multi_tenant_setup.sql

# 手动跑 004
psql -U finbp -d finbp -f infra/migrations/004_tenant_m2_super_admin_and_triggers.sql
```

**注意**：
- 003 + 004 必须按顺序跑（004 依赖 003 的 `tenants` 表）
- 4 份文件全部 idempotent（`IF NOT EXISTS` + `ON CONFLICT DO NOTHING`）
- 已 apply 的 4 份 (`001` / `002` / `003` / `004`) 在 `schema_migrations` 表里

### 5.3 状态查询（HTTP）

```bash
# 列 applied / pending / drift
curl -b /tmp/c.txt http://localhost:8000/api/admin/migrations/status | jq .

# 跑 apply（super admin 鉴权）
curl -b /tmp/c.txt -X POST http://localhost:8000/api/admin/migrations/apply | jq .

# drift 检测（SHA256 checksum 验证）
curl -b /tmp/c.txt -X POST http://localhost:8000/api/admin/migrations/verify | jq .
```

## 6. 多租户首启（v2 M1-M3）

### 6.1 自动 backfill

`infra/migrations/003_multi_tenant_setup.sql` 启动时执行：

1. 创建 `tenants` 表 + default tenant (UUID `00000000-0000-0000-0000-000000000000`)
2. 6 张业务表（`users` / `user_roles` / `user_business_lines` / `raw.audit_log` / `ai_models` / `raw.uploads`）加 `tenant_id` 列
3. 所有现有行 backfill 到 default tenant
4. NOT NULL 约束 + RLS ENABLE + FORCE + `tenant_lock` policy

**0 数据丢失**：v0.1.0 现有所有行自动到 default tenant，**业务无感**。

`infra/migrations/004_tenant_m2_super_admin_and_triggers.sql`：

1. `users.is_super_admin` 列 + 部分索引
2. 6 张表 BEFORE INSERT 触发器（自动从 GUC 读 `app.tenant_id`）
3. `admin` 用户标记 `is_super_admin = TRUE`

### 6.2 创建新 tenant

```bash
# super admin (admin) 登录
curl -c /tmp/c.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 创建 tenant
curl -b /tmp/c.txt -X POST http://localhost:8000/api/admin/tenants \
  -H "Content-Type: application/json" \
  -d '{"slug":"acme","name":"Acme Realty","plan":"enterprise"}' | jq .
```

### 6.3 切 tenant（super admin）

```bash
# 切到 acme tenant
ACME_ID="<acme uuid from response>"
curl -b /tmp/c.txt -H "X-Tenant-ID: $ACME_ID" \
  http://localhost:8000/api/auth/me-tenant | jq .
# → { "tenant_id": "<acme uuid>", "is_super_admin": true, "source": "header" }
```

### 6.4 验证 RLS 隔离

```bash
# 1. 进 postgres
docker compose -f infra/docker-compose.yml exec postgres psql -U finbp -d finbp

# 2. 设 tenant A GUC
SET LOCAL app.tenant_id = '<tenant A uuid>';
SELECT username FROM users;  -- 仅 A tenant 的用户

# 3. 设 tenant B GUC
SET LOCAL app.tenant_id = '<tenant B uuid>';
SELECT username FROM users;  -- 仅 B tenant 的用户 (与 A 隔离)
```

详细多租户设计见 [`docs/multi-tenant-deliverable.md`](docs/multi-tenant-deliverable.md)。

## 7. 后续新增环境变量

1. 在 `.env.example` 中添加变量并附上注释
2. 在 `infra/docker-compose.yml` 对应服务的 `environment:` 块中以
   `${VAR_NAME:-default}` 形式引用
3. 前端变量需以 `NEXT_PUBLIC_` 为前缀，并重新构建 web 镜像
4. 重启：`docker compose -f infra/docker-compose.yml up -d`

## 8. 生产环境加固

正式生产部署请额外完成：

- **TLS 终止**：在 `:13000`（iStoreOS 偏移后）前部署 nginx / Caddy / 云 LB
- **密钥管理**：使用 Vault（HashiCorp Vault、AWS Secrets Manager），不要
  写入磁盘的 `.env`
- **备份**：为 `postgres-data`、`clickhouse-data`、`minio-data` 数据卷
  配置备份
- **可观测性**：抓取 `/api/registry/lines` 和 `/api/copilot/health` 作为
  存活探针；将 uvicorn 日志导出至 Loki/Elasticsearch
- **资源限制**：在 compose 中设置 `deploy.resources.limits`
- **Airflow 反向代理**（`:18080` / `:8080`）—— Airflow 除 admin 用户外无内置认证
- **多租户 RLS 验证**：定期 `SELECT * FROM users;` 验证 RLS 拒绝跨租户（必须 SET LOCAL GUC）
- **国产化替代**：根据部署环境要求，Postgres（达梦）/ ClickHouse（TDengine）/
  MinIO（Ceph）等可替换

## 9. 已知限制

- Web BFF 代理在 body 侧无类型校验（直接转发浏览器发送的内容）。
  所有身份认证/授权不在其职责范围内。
- Copilot 的"真实 LLM"路径使用同步 HTTP（urllib）。流式输出 /
  function-calling / RAG 属于后续工作。
- 内存中的告警历史为进程内存储，重启后清空。
- DBT 模型引用 `raw.uploads`；真实数据源需向该表灌入数据（通过
  Airflow DAG 或 upload 端点）。
- **v2 多租户**：所有新 router **必须**走 `tenant_session()` — 直接用 `get_session_factory()` 会被 RLS 拒绝返 0 行
- **v2 migration runner**：drift 检测**不**自动重跑（防 tamper），需 admin 手动决定
- **v2 视角切换**：`X-Active-View` 是请求头（不是 URL query），BFF 必须透传
