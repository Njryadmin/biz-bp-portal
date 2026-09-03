# Architecture

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
                │ registry.yaml       │  │ dynamic importlib scan   │
                │ (single source of   │  │ apps/api/app/routers/    │
                │  truth)             │  │ registry.py              │
                └─────────┬───────────┘  └────────────┬─────────────┘
                          │                           │ include_router
                          ▼                           ▼
        ┌──────────────────────────────┐   ┌────────────────────────────┐
        │ apps/web                     │   │ apps/api                    │
        │ (Next.js 14)                 │   │ (FastAPI)                   │
        │  GET /api/registry/lines ────│──▶│  /api/registry/lines        │
        │  dynamic left nav            │   │  /api/lines/<line>/...      │
        │  UniversalKpiCard / Chart    │   │                            │
        └──────────────┬───────────────┘   └────────────┬───────────────┘
                       │ fetch                           │ asyncpg / ch / s3
                       ▼                                 ▼
        ┌─────────────────────────────────────────────────────────────┐
        │ Postgres 16 · Redis 7 · ClickHouse 24 · MinIO (S3)         │
        │ Orchestrated by Airflow 2.8 (image apache/airflow:2.8-...)  │
        │ Raw data lands in data/landing/, dbt -> ClickHouse marts.  │
        └─────────────────────────────────────────────────────────────┘
```

The whole monorepo has **one rule**: apps/* and infra/* are generic, business
lines are plugins. Discovery at startup only — no static imports across the
boundary.
