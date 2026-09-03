# 部署指南

Biz-BP Portal 以 7 服务 Docker Compose 栈的形式交付。
本文档涵盖环境变量、部署拓扑和运维命令。

## 0. 身份认证 / RBAC（2026-09-03）

所有 API 端点均由基于 JWT 的 RBAC 系统保护。首次启动时，API
会从业务线注册表自动创建账号（幂等——仅在 `users` 表为空时执行）。
完整设计参见
**[docs/rbac-2026-09-03-deliverable.md](docs/rbac-2026-09-03-deliverable.md)**。

**默认初始账号**（生产环境请通过 PATCH 或重建账号轮换密码）：

| 用户名 | 密码 | 角色 | 可见范围 |
|---|---|---|---|
| `admin` | `admin123` | `admin` + `auditor` | 全部 10 条业务线 |
| `bp-residential` | `bp123456` | `bp:residential` | 仅住宅 |
| `bp-retail` | `bp123456` | `bp:retail` | 仅零售 |
| `bp-retail-leasing` | `bp123456` | `bp:retail-leasing` | 仅零售租赁 |
| `bp-valuation` | `bp123456` | `bp:valuation` | 仅估价 |
| `bp-advisory` | `bp123456` | `bp:advisory` | 仅顾问 |
| `bp-office-leasing` | `bp123456` | `bp:office-leasing` | 仅写字楼租赁 |
| `bp-investment` | `bp123456` | `bp:investment` | 仅投资 |
| `bp-project-management` | `bp123456` | `bp:project-management` | 仅项目管理 |
| `bp-industrial` | `bp123456` | `bp:industrial` | 仅工业地产 |
| `bp-my-line` | `bp123456` | `bp:my-line` | 仅 my-line |

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
```

## 4. 后续新增环境变量

1. 在 `.env.example` 中添加变量并附上注释
2. 在 `infra/docker-compose.yml` 对应服务的 `environment:` 块中以
   `${VAR_NAME:-default}` 形式引用
3. 前端变量需以 `NEXT_PUBLIC_` 为前缀，并重新构建 web 镜像
4. 重启：`docker compose -f infra/docker-compose.yml up -d`

## 5. 生产环境加固

正式生产部署请额外完成：

- **TLS 终止**：在 `:3000` 前部署 nginx / Caddy / 云 LB
- **密钥管理**：使用 Vault（HashiCorp Vault、AWS Secrets Manager），不要
  写入磁盘的 `.env`
- **备份**：为 `postgres-data`、`clickhouse-data`、`minio-data` 数据卷
  配置备份
- **可观测性**：抓取 `/api/registry/lines` 和 `/api/copilot/health` 作为
  存活探针；将 uvicorn 日志导出至 Loki/Elasticsearch
- **资源限制**：在 compose 中设置 `deploy.resources.limits`
- **Airflow 反向代理**（`:8080`）—— Airflow 除 admin 用户外无内置认证
- **国产化替代**：根据部署环境要求，Postgres（达梦）/ ClickHouse（TDengine）/
  MinIO（Ceph）等可替换

## 6. 已知限制

- Web BFF 代理在 body 侧无类型校验（直接转发浏览器发送的内容）。
  所有身份认证/授权不在其职责范围内。
- Copilot 的"真实 LLM"路径使用同步 HTTP（urllib）。流式输出 /
  function-calling / RAG 属于后续工作。
- 内存中的告警历史为进程内存储，重启后清空。
- DBT 模型引用 `raw.uploads`；真实数据源需向该表灌入数据（通过
  Airflow DAG 或 upload 端点）。
