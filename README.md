# Biz-BP Portal

A pluggable financial-business-partner analysis portal for real-estate
consulting firms. The whole backend is built around a **business-line plugin
framework** — adding a new department (e.g. *industrial real estate*) is a
copy-and-edit job, not a code change.

```
business_lines/<line>/         ← one folder per line
  manifest.yaml                  ← name, nav, api_prefix, warehouse
  indicators.yaml                ← 8-10 KPIs
  api/router.py                  ← FastAPI endpoints
  sensitivity.yaml               ← 4 inputs × N outputs + coefficients
  forecast.yaml                  ← time-series definitions
  alerts.yaml                    ← rules + thresholds
  dbt/models/                    ← staging + marts SQL
  data/seed/                     ← mock data (real-data swap point)
```

Four universal engines read these YAML files at runtime — no `import` of
business-line code anywhere in `apps/` or `infra/`:

| Engine | What it does |
|---|---|
| **Sensitivity Lab** | 2-factor heatmap + tornado + scenario comparison |
| **AI Copilot** | Natural-language Q&A over all lines (pluggable LLM: DeepSeek / Ollama / Mock) |
| **Rolling Forecast** | 12-month projection with MAPE + deviation attribution |
| **Alert Center** | Rule engine with severity + acknowledge + history |

Plus an **scraper framework** (NBS 70-city index, Lianjia deals, policy
crawler) for real-data ingestion.

---

## Quick start (Docker)

```bash
# 1. Copy and edit env (the only required secret is DEEPSEEK_API_KEY for real LLM)
cp .env.example .env

# 2. Build + start everything
docker compose -f infra/docker-compose.yml --env-file .env up -d --build

# 3. Open the portal
open http://localhost:3000
```

That's it. 7 services come up:
- **Web** (Next.js prod): http://localhost:3000
- **API** (FastAPI): http://localhost:8000
- **Airflow**: http://localhost:8080 (admin/admin)
- **Postgres**: localhost:5432
- **Redis**: localhost:6379
- **ClickHouse**: localhost:8123 (HTTP), localhost:9100 (native)
- **MinIO**: localhost:9001 (console, finbp/finbp12345)

See **[DEPLOY.md](DEPLOY.md)** for production-grade deployment,
troubleshooting, and the full env-var reference.

---

## Authentication

All routes are protected by an RBAC (role-based access control) layer.
On the very first boot, the API auto-creates the following accounts
from the business-line registry (idempotent — only runs when `users` is empty):

| Username | Password | Role | Sees |
|---|---|---|---|
| `admin` | `admin123` | `admin` + `auditor` | everything |
| `viewer` | — (set via API) | `viewer` | everything, read-only |
| `bp-<line>` | `bp123456` | `bp:<line>` | only that line |

Change these defaults in production via `BIZ_BP_BOOTSTRAP_ADMIN_PASSWORD` /
`BIZ_BP_BOOTSTRAP_BP_PASSWORD` env vars or via
`PATCH /api/auth/users/{id}/roles` after first boot.

Key endpoints:

- `POST /api/auth/login` — body `{username, password}` → httpOnly cookie `finbp_token`
- `POST /api/auth/logout` — clear cookie
- `GET  /api/auth/me` — current user + roles + accessible_lines
- `GET  /api/auth/accessible-lines` — business lines visible to me
- `GET  /api/auth/users` (admin) / `POST` (admin) / `PATCH /users/{id}/roles` (admin)
- `GET  /api/auth/audit-log` (admin/auditor) — paginated request log

Business-line enforcement: a user with `bp:residential` cannot read
`/api/lines/retail/*` (403), the registry list returned from
`/api/registry/lines` is pre-filtered, and the dashboard sidebar only
shows the lines the user can access.

See **[docs/rbac-2026-09-03-deliverable.md](docs/rbac-2026-09-03-deliverable.md)**
for the full design + 15 curl scenarios + bootstrap walkthrough.

---

## Quick start (Local dev)

```bash
# 1. Install
npm install
cd apps/api && pip install -e ".[dev]" && cd ../..

# 2. Start infra (Postgres, Redis, ClickHouse, MinIO, Airflow)
docker compose -f infra/docker-compose.yml up -d postgres redis minio

# 3. Start API (port 8769)
$env:PYTHONPATH = "$(pwd)/apps/api"
python -m uvicorn app.main:app --app-dir apps/api --port 8769 --reload

# 4. Start web (port 3000)
npm run web:dev
```

Open http://localhost:3000.

---

## Business lines shipped

| ID | Display name | Domain |
|---|---|---|
| `residential` | 住宅分析 | Sales / IRR / red-lines / payment |
| `retail` | 零售分析 | NOI / efficiency / brand-mix / renovation NPV |
| `retail-leasing` | 零售租赁与市场报告 | Leasing deals / market benchmark |
| `valuation` | 估价部 | Reports / accuracy / collection |
| `advisory` | 地产顾问部 | Projects / renewal / clients |
| `office-leasing` | 写字楼租赁部 | Deals / buildings / brokers |
| `investment` | 地产投资部 | Funds / portfolio / exits |
| `project-management` | 地产项目管理部 | Progress / budget / satisfaction |
| `industrial` | 工业地产部 | Warehouses / occupancy / tenants |
| `my-line` | (demo) | Play with the plugin mechanism |

To add an 11th line, see **[business_lines/README.md](business_lines/README.md)** — it's a 5-step copy-and-edit flow.

---

## Project layout

```
fin-bp-portal/
├── apps/
│   ├── api/                  # FastAPI + 4 engines + scraper framework
│   │   ├── app/
│   │   │   ├── services/
│   │   │   │   ├── sensitivity_engine.py
│   │   │   │   ├── copilot_engine.py      + llm/{base,mock,deepseek,ollama,prompts}.py
│   │   │   │   ├── forecast_engine.py
│   │   │   │   ├── alert_engine.py
│   │   │   │   └── scrapers/{base,registry,utils,scrapers/*}
│   │   │   ├── routers/{registry,sensitivity,copilot,forecast,alerts,scrapers,upload}.py
│   │   │   └── main.py
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── tests/                      # 85+ tests
│   └── web/                  # Next.js 14 + Ant Design 5 + ECharts 5
│       ├── app/
│       │   ├── (dashboard)/{dashboard,sensitivity,copilot,forecast,alerts,scrapers,[line],[line]/[page]}
│       │   └── api/                    # BFF proxies
│       ├── Dockerfile
│       └── package.json
├── business_lines/           # Plugin: 10 lines × 8 files each
├── packages/
│   ├── types/                # Shared TypeScript types
│   └── ui/                   # UniversalKpiCard, UniversalChart, EmptyState, RoleSwitcher
├── infra/
│   ├── docker-compose.yml    # 7-service stack
│   ├── airflow/dags/
│   ├── dbt/                  # DBT models
│   └── .env.example
├── data/landing/             # CSV/Excel/JSON drop zone
├── docs/                     # All deliverables
├── .env.example              # Env-var template
├── .dockerignore
├── .gitignore
└── README.md
```

---

## Architecture commitments

These are enforced by the codebase and verified in `docs/architecture-audit-*.md`:

1. **No `business_lines/*` imports** anywhere in `apps/` or `infra/`
2. **Adding a new business line = 0 lines of core code**
3. **4 universal engines** work for any line that has its YAML configs
4. **LLM is pluggable** with `MockBackend` fallback when `DEEPSEEK_API_KEY` is absent
5. **DBT and scrapers** also auto-discover via directory scanning

---

## License

Internal use.
