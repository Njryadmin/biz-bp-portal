# infra/ — 基础设施运维

> 这个目录装的是**生产部署**用的编排 / 数据集成 / DBT 模板。
> **本地 dev 用不到**——本地用 `apps/api/pgserver_runner.py` 嵌入式 PG。
> 配套：[`DEPLOY.md`](../DEPLOY.md) — 完整生产部署指南。

---

## 目录结构

```
infra/
├── README.md                      ← 你正在读
├── docker-compose.yml             ← 7 服务栈（api / web / postgres / redis / clickhouse / minio / airflow）
├── airflow/
│   └── dags/
│       ├── ingest_daily.py        ← 每日落地区 → raw.uploads
│       └── scrape_weekly.py       ← 每周跑 3 个爬虫
├── dbt/
│   ├── dbt_project.yml            ← 全局 DBT 项目
│   └── profiles.yml               ← DBT profile（dev / prod）
├── .env.example                   ← 环境变量模板（部署时 cp .env.example .env）
└── data-deliverable.md            ← 旧交付说明
```

---

## 7 服务栈

启动：

```bash
cd infra/
cp .env.example .env
# 编辑 .env：填入 DEEPSEEK_API_KEY / JWT_SECRET / POSTGRES_PASSWORD 等
docker compose --env-file ../.env up -d --build
```

| 服务 | 端口（host:container） | 镜像 | 用途 |
|---|---|---|---|
| **api** | 8000:8000 | `finbp/api:0.1.0` | FastAPI/uvicorn |
| **web** | 3000:3000 | `finbp/web:0.1.0` | Next.js 生产模式 |
| **postgres** | 5432:5432 | `postgres:16-alpine` | 主数据库 |
| **redis** | 6379:6379 | `redis:7-alpine` | 缓存 / 队列 |
| **clickhouse** | 8123:8123 / 9100:9000 | `clickhouse/clickhouse-server:24.3-alpine` | 分析（可选） |
| **minio** | 9000:9000 / 9001:9001 | `minio/minio:latest` | S3 文件存储 |
| **airflow** | 8080:8080 | `apache/airflow:2.8-python11` | DAG 调度 |

启动顺序由 `depends_on: condition: service_healthy` 强制：
- `postgres` healthy → `api` 起
- `api` healthy → `web` 起
- `redis` / `minio` / `clickhouse` / `airflow` 是兄弟节点

---

## 访问入口（生产）

| 服务 | URL | 默认凭据 |
|---|---|---|
| **Web** | <http://localhost:3000> | admin / admin123（首启动默认） |
| **API** | <http://localhost:8000> | — |
| **Airflow** | <http://localhost:8080> | admin / admin |
| **MinIO 控制台** | <http://localhost:9001> | finbp / finbp12345 |
| **Postgres** | `localhost:5432` | finbp / finbp |
| **ClickHouse** | `localhost:8123`（HTTP） | finbp / finbp |

**容器命名**沿用历史 `finbp-` 前缀（与 `service_token` / `POSTGRES_USER` 一致）。
如需一并改成 `bizbp-`，同步修改 `docker-compose.yml` + `airflow/dags/*.py` + `.env.example`。

---

## Airflow DAG

### `ingest_daily`（每日）

读 `data/landing/*`（Excel / CSV / 银行流水），解析后灌入 `raw.uploads`。

触发方式：
- 自动：cron `0 2 * * *`
- 手动：Airflow UI → DAGs → `ingest_daily` → Trigger

### `scrape_weekly`（每周）

调 3 个爬虫（NBS / 链家 / 政策），结果灌入 `raw.uploads`（`upload_type='scraper'`）。

触发方式：
- 自动：cron `0 6 * * 1`（每周一早上 6 点）
- 手动：Airflow UI → DAGs → `scrape_weekly` → Trigger

> 单独跑爬虫可以用 API（参见 [`docs/maintenance/operations.md`](../docs/maintenance/operations.md) §3）。

### 加新 DAG

1. 写 `infra/airflow/dags/<name>.py`
2. 用 `from airflow import DAG`
3. 容器会**自动**扫到（不需要重启 airflow）
4. 进 Airflow UI → DAGs → 启用（默认禁用）

---

## DBT

### 项目结构

```
infra/dbt/
├── dbt_project.yml     ← profile 指向 ../profiles.yml
├── profiles.yml        ← target dev (localhost) / prod (compose 服务名)
├── models/
│   ├── staging/        ← stg_* 视图
│   ├── intermediate/   ← int_* 视图
│   └── marts/          ← mart_* 视图（业务线指标）
└── seeds/              ← 静态查找表
```

### 跑

```bash
# 装 dbt（一次性）
pip install dbt-postgres

# 编译检查
cd infra/dbt
dbt parse

# 跑
dbt run --project-dir .

# 只跑某个 schema
dbt run --project-dir . --select stg_residential

# 跑 + 测试
dbt build --project-dir .
```

### 连到哪个 Postgres

`profiles.yml` 里的 target：
- `dev` → `localhost:5432`（本地 dev 嵌入式 pgserver）
- `prod` → `postgres:5432`（compose 容器）

切换：

```bash
dbt run --project-dir . --target prod
```

---

## 嵌入业务线的 DBT 模型

每条业务线有**自己的** DBT 项目（在 `business_lines/<line>/dbt/`），
**与** `infra/dbt/`（全局）并存。

业务线 DBT 在容器里通过 volume mount：

```yaml
# docker-compose.yml（airflow 段）
volumes:
  - ../business_lines:/opt/airflow/business_lines:ro
```

跑业务线 DBT：

```bash
# 在 airflow 容器内
dbt run --project-dir /opt/airflow/business_lines/residential
```

---

## 常见操作

### 看所有服务状态

```bash
docker compose -f infra/docker-compose.yml ps
```

### 重启单个服务

```bash
# api
docker compose -f infra/docker-compose.yml restart api

# 重新构建（改了 Dockerfile 或 package.json）
docker compose -f infra/docker-compose.yml up -d --build api
```

### 看日志

```bash
# 所有
docker compose -f infra/docker-compose.yml logs -f

# 只 api
docker compose -f infra/docker-compose.yml logs -f api
```

### 进入容器

```bash
# api 容器（debug 各种）
docker compose -f infra/docker-compose.yml exec api bash

# postgres
docker compose -f infra/docker-compose.yml exec postgres psql -U finbp -d finbp
```

### 全部停止

```bash
# 保留数据卷
docker compose -f infra/docker-compose.yml down

# 删除数据卷（危险）
docker compose -f infra/docker-compose.yml down -v
```

---

## 备份

```bash
# 导出（Postgres）
docker compose -f infra/docker-compose.yml exec -T postgres \
  pg_dump -U finbp -d finbp -Fc > backup_$(date +%Y%m%d).dump

# 还原
cat backup_20260903.dump | docker compose -f infra/docker-compose.yml exec -T postgres \
  pg_restore -U finbp -d finbp --clean --if-exists

# MinIO 数据
docker compose -f infra/docker-compose.yml exec minio sh -c "mc mirror /data /backup"
```

---

## 与本地 dev 的差异

| 维度 | 本地 dev | 生产 compose |
|---|---|---|
| **Postgres** | 嵌入式 `pgserver` (端口 11667) | `postgres:16-alpine` (5432) |
| **Redis** | 没有（API 自己处理） | `redis:7-alpine` |
| **ClickHouse** | 没有 | `clickhouse/clickhouse-server:24.3-alpine` |
| **MinIO** | 没有 | `minio/minio` |
| **Airflow** | 没有 | `apache/airflow:2.8-python11` |
| **API 端口** | 8769 | 8000 |
| **启动方式** | 3 个独立 PowerShell 窗口 | `docker compose up -d` |
| **重启影响** | 各服务独立 | `depends_on` 强制顺序 |

**BIZ_BP_* 环境变量在两边都生效**——本地 dev 走 `.env`（或 shell env），
生产走 `docker-compose.yml` 的 `${VAR:-default}` 插值。

---

## 国产化替代

按 `DEPLOY.md` §5 提示：

- **Postgres**（达梦 DM8）：需要改 DBT profile + SQLAlchemy `database_url`
- **ClickHouse**（TDengine）：需要改 DBT adapter + scrapers 的写入目标
- **MinIO**（Ceph）：S3 协议兼容，仅需改 endpoint URL

详细步骤在生产加固文档中。
