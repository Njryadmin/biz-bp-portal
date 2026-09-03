# 架构

```
                  ┌─────────────────────────────────────────────┐
                  │             business_lines/                 │
                  │  _template/   <line_a>/   <line_b>/   ...   │
                  │     │           │            │              │
                  │     ├─ manifest.yaml  (id, nav, api_prefix)  │
                  │     ├─ indicators.yaml                      │
                  │     ├─ web/pages/*.tsx                      │
                  │     ├─ api/router.py   (APIRouter)          │
                  │     └─ dbt/models/*.sql                     │
                  └────────────┬───────────────┬────────────────┘
                               │ read          │ read
                               ▼               ▼
                ┌─────────────────────┐  ┌──────────────────────────┐
                │ registry.yaml       │  │ 动态 importlib 扫描      │
                │ (单一来源)          │  │ apps/api/app/routers/    │
                │                     │  │ registry.py              │
                └─────────┬───────────┘  └────────────┬─────────────┘
                          │                           │ include_router
                          ▼                           ▼
        ┌──────────────────────────────┐   ┌────────────────────────────┐
        │ apps/web                     │   │ apps/api                    │
        │ (Next.js 14)                 │   │ (FastAPI)                   │
        │  GET /api/registry/lines ────│──▶│  /api/registry/lines        │
        │  动态左侧导航                │   │  /api/lines/<line>/...      │
        │  UniversalKpiCard / Chart    │   │                            │
        └──────────────┬───────────────┘   └────────────┬───────────────┘
                       │ fetch                           │ asyncpg / ch / s3
                       ▼                                 ▼
        ┌─────────────────────────────────────────────────────────────┐
        │ Postgres 16 · Redis 7 · ClickHouse 24 · MinIO (S3)         │
        │ 由 Airflow 2.8 编排（镜像 apache/airflow:2.8-...）          │
        │ 原始数据落在 data/landing/，dbt -> ClickHouse marts。       │
        └─────────────────────────────────────────────────────────────┘
```

整个 monorepo 遵循**一条规则**：`apps/*` 和 `infra/*` 是通用代码，
业务线是插件。仅在启动时发现——边界之间不做静态 import。
