# Deployment Guide

The Fin BP Portal is delivered as a 7-service Docker Compose stack.
This document covers environment variables, deployment topology, and
operational commands.

## 0. Authentication / RBAC (2026-09-03)

All API endpoints are gated by the JWT-based RBAC system. On the very
first boot, the API auto-creates accounts from the business-line
registry (idempotent — only runs when `users` is empty). See
**[docs/rbac-2026-09-03-deliverable.md](docs/rbac-2026-09-03-deliverable.md)**
for the full design.

**Default initial accounts** (rotate in production via PATCH or
re-create):

| Username | Password | Role | Sees |
|---|---|---|---|
| `admin` | `admin123` | `admin` + `auditor` | all 10 lines |
| `bp-residential` | `bp123456` | `bp:residential` | residential only |
| `bp-retail` | `bp123456` | `bp:retail` | retail only |
| `bp-retail-leasing` | `bp123456` | `bp:retail-leasing` | retail-leasing only |
| `bp-valuation` | `bp123456` | `bp:valuation` | valuation only |
| `bp-advisory` | `bp123456` | `bp:advisory` | advisory only |
| `bp-office-leasing` | `bp123456` | `bp:office-leasing` | office-leasing only |
| `bp-investment` | `bp123456` | `bp:investment` | investment only |
| `bp-project-management` | `bp123456` | `bp:project-management` | project-management only |
| `bp-industrial` | `bp123456` | `bp:industrial` | industrial only |
| `bp-my-line` | `bp123456` | `bp:my-line` | my-line only |

**Required env vars** (add to `.env`):

```bash
# 32+ chars, generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET=change-me-in-production-32-chars-min

# Optional overrides
JWT_ALGORITHM=HS256
JWT_EXPIRY_HOURS=24
FIN_BP_BOOTSTRAP_ADMIN_USERNAME=admin
FIN_BP_BOOTSTRAP_ADMIN_PASSWORD=admin123       # change in prod
FIN_BP_COOKIE_SECURE=false                     # set true in prod (HTTPS only)
FIN_BP_COOKIE_NAME=finbp_token
```

**Smoke test** (after `docker compose up -d`):

```bash
# 1. 401 without auth
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/registry/lines
# → 401

# 2. login as admin
curl -s -c /tmp/c.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
# → 200, cookie written to /tmp/c.txt

# 3. registry with cookie
curl -s -b /tmp/c.txt http://localhost:8000/api/registry/lines | jq '.lines | length'
# → 10

# 4. login as bp-residential
curl -s -c /tmp/c.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"bp-residential","password":"bp123456"}' >/dev/null
curl -s -b /tmp/c.txt http://localhost:8000/api/registry/lines | jq '.lines | length'
# → 1  (only residential)

# 5. cross-line access denied
curl -s -b /tmp/c.txt -o /dev/null -w "%{http_code}\n" \
  http://localhost:8000/api/lines/retail/indicators
# → 403
```

**Audit log**: every authenticated request is recorded in
`raw.audit_log`. Query as admin or auditor:

```bash
curl -s -b /tmp/c.txt 'http://localhost:8000/api/auth/audit-log?limit=10'
# Returns { count, items: [{id, user_id, username, method, path, status_code, ...}, ...] }
```

Retention: the table grows unbounded. Schedule a daily cron (e.g. via
Airflow) to `DELETE FROM raw.audit_log WHERE "timestamp" < NOW() - INTERVAL '90 days';`
in production.

## 1. Topology

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser → http://localhost:3000                                │
└─────────────────┬───────────────────────────────────────────────┘
                  │ (Next.js SSR + BFF proxy /api/* → :8000)
                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  web (Next.js 14, production)            :3000                    │
│  ├─ reads NEXT_PUBLIC_API_BASE_URL=http://api:8000             │
└─────────────────┬───────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  api (FastAPI + uvicorn)               :8000                     │
│  ├─ 4 universal engines (Sensitivity, Copilot, Forecast, Alts)│
│  ├─ 3 scrapers (NBS, Lianjia, Policy)                         │
│  ├─ dynamic business-line discovery via importlib             │
│  └─ FIN_BP_API_BASE=http://api:8000 (self-call for engines)  │
└─────────────────┬───────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  Airflow (scheduler + webserver)     :8080                      │
│  └─ runs ingest_daily + scrape_weekly DAGs                    │
└─────────────────┬───────────────────────────────────────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
   Postgres           ClickHouse
    :5432              :8123/:9100
                  (MinIO :9000/:9001 for file storage)
                  (Redis :6379 for cache)
```

## 2. Environment variables

> Copy `.env.example` to `.env` and fill in real values.
> The api service reads vars from the compose `environment:` block (which
> interpolates from `.env` via `${VAR:-default}`). The web service receives
> vars at *build time* via `NEXT_PUBLIC_*` (Next.js convention).

### 2.1 Required for any deployment

| Var | Where | Default | Purpose |
|---|---|---|---|
| `FIN_BP_PROJECT_ROOT` | api | `/app` | Path to project root inside container |
| `FIN_BP_API_BASE` | api | `http://api:8000` | How engines call back to the API |
| `FIN_BP_DATABASE_URL` | api | (built from PG creds) | asyncpg connection string |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | compose | finbp/finbp/finbp | Postgres credentials |
| `NEXT_PUBLIC_API_BASE_URL` | web (build-time) | `http://api:8000` | Where the BFF proxies forward |

### 2.2 AI Copilot LLM (optional but recommended for real Q&A)

| Var | Default | Effect |
|---|---|---|
| `DEEPSEEK_API_KEY` | empty | If set, Copilot uses DeepSeek V3 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | Override for OpenAI-compatible providers |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model name |
| `DEEPSEEK_TIMEOUT` | `30` | Seconds |
| `DEEPSEEK_TEMPERATURE` | `0.3` | Sampling temperature |
| `OLLAMA_BASE_URL` | empty | If set (and `DEEPSEEK_API_KEY` empty), uses Ollama |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Ollama model |
| `OLLAMA_TIMEOUT` | `30` | Seconds |

**Factory precedence**: `DEEPSEEK_API_KEY` → `OLLAMA_BASE_URL` → MockBackend.
**Auto fallback**: If DeepSeek returns 401/network error, the response sets
`used_fallback: true` and serves the rule-engine answer (HTTP 200, never 500).

### 2.3 Airflow / Data integration

| Var | Default | Purpose |
|---|---|---|
| `AIRFLOW__CORE__FERNET_KEY` | random 44-char base64 | Airflow secrets encryption |
| `AIRFLOW_ADMIN_USERNAME` / `AIRFLOW_ADMIN_PASSWORD` | admin/admin | Webserver login |
| `DBT_HOST` / `DBT_PORT` / `DBT_USER` / `DBT_PASSWORD` / `DBT_DBNAME` | from PG | dbt profile connection |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | finbp / finbp12345 | S3 storage credentials |

### 2.4 ClickHouse / Redis (optional, for analytics + cache)

| Var | Default |
|---|---|
| `CLICKHOUSE_DB` / `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` | finbp / finbp / finbp |
| `REDIS_HOST` / `REDIS_PORT` | redis / 6379 |

## 3. Deployment commands

### 3.1 First-time setup

```bash
git clone <repo-url> fin-bp-portal
cd fin-bp-portal

# 1. Configure secrets
cp .env.example .env
# Edit .env — at minimum set DEEPSEEK_API_KEY if you have one

# 2. Build + start
docker compose -f infra/docker-compose.yml --env-file .env up -d --build

# 3. Wait for healthchecks (api/web wait for postgres)
docker compose -f infra/docker-compose.yml ps

# 4. Open
open http://localhost:3000
```

### 3.2 Common operations

```bash
# Tail all logs
docker compose -f infra/docker-compose.yml logs -f

# Tail only app logs
docker compose -f infra/docker-compose.yml logs -f api web

# Restart just the api after a code change
docker compose -f infra/docker-compose.yml restart api

# Rebuild and restart api/web
docker compose -f infra/docker-compose.yml up -d --build api web

# Stop everything (data volumes preserved)
docker compose -f infra/docker-compose.yml down

# Nuke everything including data
docker compose -f infra/docker-compose.yml down -v
```

### 3.3 Health checks

```bash
# API health
curl -fsS http://localhost:8000/api/registry/lines | jq

# Web health
curl -fsS http://localhost:3000/ -o /dev/null -w "%{http_code}\n"

# Database
docker compose -f infra/docker-compose.yml exec postgres pg_isready -U finbp

# Airflow
curl -fsS http://localhost:8080/health
```

## 4. Adding environment variables later

1. Add the var to `.env.example` with documentation
2. Reference it in `infra/docker-compose.yml` under the relevant service's
   `environment:` block using `${VAR_NAME:-default}` syntax
3. For frontend vars, prefix with `NEXT_PUBLIC_` and rebuild the web image
4. Restart: `docker compose -f infra/docker-compose.yml up -d`

## 5. Production hardening

For a real production deployment, additionally:

- **TLS termination** in front of `:3000` (nginx / Caddy / cloud LB)
- **Secrets** in a vault (HashiCorp Vault, AWS Secrets Manager), not in
  `.env` on disk
- **Backups** for `postgres-data`, `clickhouse-data`, `minio-data` volumes
- **Observability**: scrape `/api/registry/lines` and `/api/copilot/health`
  for uptime; export uvicorn logs to Loki/Elasticsearch
- **Resource limits** in compose (`deploy.resources.limits`)
- **Reverse proxy** for Airflow (`:8080`) — it has no built-in auth beyond
  the admin user
- **国产化替代** for postgres (达梦) / ClickHouse (TDengine) / MinIO
  (Ceph) if required by the deployment environment

## 6. Known limitations

- The web BFF proxies are untyped on the body side (forwards whatever the
  browser sent). All authn/z is out of scope.
- Copilot "real LLM" path uses synchronous HTTP (urllib). Streaming /
  function-calling / RAG is future work.
- In-memory alert history is process-local; restart wipes it.
- DBT models reference `raw.uploads`; real data sources must populate that
  table (via Airflow DAGs or the upload endpoint).
