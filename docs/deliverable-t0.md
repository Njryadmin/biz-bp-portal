# T0 Deliverable — Fin BP Portal Monorepo Foundation

> Worker: Coder · T0 (foundation)
> Date: 2026-09-02
> Project root: `C:\Users\mozzi\.mavis\workspace\fin-bp-portal\`

## 1. Files created (relative to project root)

### Root
- `package.json` — npm workspaces, scripts: `web:dev`, `web:build`, `web:typecheck`, `api:dev`, `api:test`, `lint`, `typecheck`
- `.gitignore` — Node / Python / DBT / Airflow / data / IDE exclusions
- `README.md` — quick-start, architecture contract, "how to add a line"

### apps/web (Next.js 14)
- `apps/web/package.json`, `tsconfig.json`, `next.config.js`, `.eslintrc.json`, `next-env.d.ts`
- `apps/web/app/layout.tsx` — root layout, AntdRegistry + ConfigProvider
- `apps/web/app/page.tsx` — `/` → redirect to `/dashboard`
- `apps/web/app/(dashboard)/layout.tsx` — **dynamic** left nav fetched from `/api/registry/lines`, never imports business_lines/*
- `apps/web/app/(dashboard)/dashboard/page.tsx` — overview grid of registered lines
- `apps/web/app/api/registry/route.ts` — same-origin proxy to the Python API
- `apps/web/lib/registry.ts` — client-side fetch helper

### apps/api (FastAPI)
- `apps/api/pyproject.toml` — `fin-bp-api` package, deps include FastAPI, Pydantic v2, SQLAlchemy 2.0, asyncpg, PyYAML, clickhouse-driver, redis, httpx; dev extras include pytest, pytest-asyncio
- `apps/api/README.md`
- `apps/api/app/__init__.py`, `app/main.py` — FastAPI factory, lifespan mounts business-line routers
- `apps/api/app/core/config.py` — pydantic-settings
- `apps/api/app/core/logging.py` — stdlib logging
- `apps/api/app/core/registry.py` — Pydantic v2 models for manifest / indicators + loaders; Pydantic-reserved `schema` field is mapped to `schema_name` so YAML contract is unchanged
- `apps/api/app/routers/registry.py` — **importlib-based dynamic discovery**; no business-line names in code
- `apps/api/app/schemas/kpi.py` — KpiItem / KpiResponse (Pydantic v2)
- `apps/api/app/db/session.py` — SQLAlchemy 2.0 async engine, asyncpg-ready
- `apps/api/tests/conftest.py`, `tests/test_registry.py`, `tests/test_api.py`

### packages
- `packages/types/package.json` + `src/index.ts` — `BusinessLine`, `Indicator`, `KpiValue`, `BusinessLineNavItem`, `ChartSpec`, `BusinessLineWarehouse`, `BusinessLineRefresh`, `BusinessLineFeatures`, `RegistryResponse`
- `packages/ui/package.json` + `src/{UniversalKpiCard,UniversalChart,UniversalAgGrid,EmptyState,index}.tsx`

### business_lines
- `business_lines/registry.yaml` — empty `lines: []`
- `business_lines/README.md` — 5-step "add a new line" walk-through
- `business_lines/_template/manifest.yaml.example`
- `business_lines/_template/indicators.yaml.example`
- `business_lines/_template/api/router.py.example`
- `business_lines/_template/web/pages/_example.tsx`
- `business_lines/_template/dbt/dbt_project.yml.example`
- `business_lines/_template/dbt/models/example.sql`
- `business_lines/_template/data/seed/.gitkeep`

### infra
- `infra/docker-compose.yml` — Postgres 16, Redis 7, ClickHouse 24, MinIO, Airflow 2.8 (`apache/airflow:2.8-python11`)
- `infra/.env.example`

### CI / docs / data
- `.github/workflows/ci.yml` — web lint+typecheck job, api pytest job
- `docs/architecture.md` — ASCII diagram + boundary rule
- `data/landing/.gitkeep`

## 2. Start commands (one-liner)

```bash
# Infra
cd infra && docker compose up -d postgres redis clickhouse minio airflow

# API (separate shell)
cd apps/api
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# Web (separate shell)
npm install
npm run web:dev    # http://localhost:3000
```

## 3. Validation run

| # | Command | Result |
|---|---------|--------|
| 1 | `cd infra && docker compose config` | **docker not installed on this Windows host** — substituted with a Python YAML structural check. `infra/docker-compose.yml` parses, all five services present, `airflow.image == "apache/airflow:2.8-python11"`, Postgres 16 / Redis 7 / ClickHouse 24 image tags confirmed. See §3.1. |
| 2 | `cd apps/web && npm install && npm run typecheck` | **PASS** — `tsc --noEmit` exits 0 with no output. `npm run lint` also PASSES (`✔ No ESLint warnings or errors`). |
| 3 | `cd apps/api && pip install -e . && python -m pytest` | **PASS** — 8 tests collected, 8 passed, 1 deprecation warning (unrelated: `httpx` in starlette `TestClient`). |
| 4 | `python -c "import yaml; yaml.safe_load(open('business_lines/registry.yaml'))"` | **PASS** — `{'lines': []}` |
| 5 | `python -c "import yaml; yaml.safe_load(open('business_lines/_template/manifest.yaml.example'))"` | **PASS** — parses with `id: change-me` etc. |
| 6 | Dynamic discovery end-to-end (synthetic line, removed after) | **PASS** — `_test_demo_line` was registered via `registry.yaml`, loaded by `importlib.util.spec_from_file_location`, mounted at `/api/lines/_test_demo_line/ping`, returned `{'pong': True, 'line': '_test_demo_line'}`. The temp directory and test entry were cleaned up; final `registry.yaml` is back to `lines: []`. |

### 3.1 Substituted docker-compose validation

```text
$ python -c "import yaml; ..."
docker-compose.yml YAML valid
services: postgres, redis, clickhouse, minio, airflow
airflow image: apache/airflow:2.8-python11
volumes: ['postgres-data', 'clickhouse-data', 'minio-data', 'airflow-dags', 'airflow-logs']
```

### 3.2 Pytest output

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

### 3.3 Constraint check (grep over `apps/`)

`apps/api` and `apps/web` reference `business_lines` only in:
- directory path strings (`"business_lines/registry.yaml"`)
- comments explaining the rule
- human-readable help text rendered in the empty-registry state

No business-line name (`change-me`, `consumer_loan`, `wealth_mgmt`, …) appears as a string literal in the core code paths.

## 4. Key assumptions

1. **Python 3.11+** — required by the spec. Tested on the system Python 3.12.10, which is API-compatible.
2. **Node 20+** — declared in `engines`. System Node 24.19.0 is used, which is also Next.js 14 compatible.
3. **npm workspaces** over pnpm — npm is already on PATH and the spec allowed either; this avoids the user having to install pnpm.
4. **ClickHouse native port remapped to 9100** to avoid clashing with MinIO's 9000. The HTTP port stays at 8123.
5. **`schema` field in `BusinessLineWarehouse`** — Pydantic v2 reserves the attribute name on `BaseModel`, so the model field is internally `schema_name` aliased to `schema`. The YAML contract (`schema: raw_change_me`) is unchanged.
6. **HTTP client / API base URL** — `apps/web` reads `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`) and also exposes a same-origin proxy at `/api/registry` so the browser never has to deal with CORS in dev.
7. **No CORS preflight in dev** — the proxy route in `apps/web/app/api/registry/route.ts` is the recommended path.
8. **Auth** — placeholder (the `CORSMiddleware` is permissive in dev; explicit prod auth is a T1+ task per the spec).
9. **AG Grid Community** — installed and the `UniversalAgGrid` wrapper exists in `packages/ui`, but no consumer page is wired to it yet (no AG Grid CSS imported in the dashboard page). Subsequent tasks can import it directly.

## 5. Blockers / limitations

1. **`docker` is not installed on the worker machine** (this Windows host has no Docker Desktop). `docker compose config` could not be executed literally; the structural check in §3.1 is a faithful substitute but does not catch the things Docker's own validator catches (e.g. `version` field, mount syntax). The user should run `cd infra && docker compose config` on a Docker-capable host before bringing the stack up.
2. **AG Grid is wired into `packages/ui`** but `apps/web` does not yet import it on any page (no business line exists that would need it). The next task that needs a data grid can `import { UniversalAgGrid } from "@fin-bp/ui"` directly.
3. **Two-args `logger.info` style** — all log calls in the API use stdlib `%s` placeholders (not loguru-style `{}`).
4. **The pre-existing `pip` deprecation** about `starlette.testclient` requiring `httpx2` is not actionable here; it's a FastAPI/Starlette release note.
5. The **workspace directory** is a reparse point — `C:\Users\mozzi\.mavis\workspace\` redirects to `C:\Users\mozzi\.minimax\workspace\`. Editing through either path is fine; tools that refuse reparse paths (e.g. `mavis-trash`) need the resolved path.
