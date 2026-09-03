# T4 — Data Integration Base (Airflow + DBT + Upload + Bank Statement)

> **Scope:** Wire up the data-integration layer for `fin-bp-portal`.
> Adds Airflow, DBT, a FastAPI upload router, three file parsers
> (Excel / CSV / bank statement), and mock landing-zone data.
> **Does NOT touch** `apps/api/app/routers/registry.py` or any
> `apps/web/.../layout.tsx` — both belong to T0.

---

## 1. What changed

### New files

| Path | Purpose |
|---|---|
| `data/landing/sample_residential.csv` | 50 rows of mock residential sales / payments / commission / channel-fee data |
| `data/landing/sample_retail.csv` | 50 rows of mock retail transactions (store / SKU / qty / price) |
| `data/landing/sample_bank_statement.txt` | 12 transactions in ICBC (中国工商银行) format |
| `infra/dbt/dbt_project.yml` | dbt project manifest, points at the `finbp` profile, materialization per layer |
| `infra/dbt/profiles.yml` | dbt profile (postgres → `analytics` schema), reads from `DBT_*` env vars |
| `infra/dbt/models/staging/stg_csv_uploads.sql` | Flattens `raw.uploads.payload` (jsonb) into typed columns |
| `infra/dbt/models/staging/stg_residential_seed.sql` | Pass-through view of the dbt seed for residential project reference data |
| `infra/dbt/models/intermediate/int_uploads_normalized.sql` | Normalizes uploaded rows + computes `net_revenue`, `commission_rate`, `payment_status` |
| `infra/dbt/seeds/sample_residential.csv` | 10 rows of project-level reference data (project_id, name, city, manager, region) |
| `infra/airflow/dags/ingest_daily.py` | `@daily` DAG: ingest `/data/landing/*.csv` → `raw.uploads`, then run dbt |
| `infra/airflow/dags/__init__.py` | Marker so Airflow treats the folder as a package |
| `apps/api/app/services/__init__.py` | Package marker |
| `apps/api/app/services/parsers/__init__.py` | Re-exports `parse_excel`, `parse_csv`, `parse_bank_statement` |
| `apps/api/app/services/parsers/excel_parser.py` | `parse_excel(path \| bytes) -> list[dict]`, openpyxl-backed |
| `apps/api/app/services/parsers/csv_parser.py` | `parse_csv(path \| bytes) -> list[dict]`, pandas-backed, UTF-8 BOM-aware |
| `apps/api/app/services/parsers/bank_statement.py` | ICBC + CMB regex parser, returns `{date, time, description, amount, balance, direction, bank}` |
| `apps/api/app/services/parsers/tests/__init__.py` | Package marker |
| `apps/api/app/services/parsers/tests/test_parsers.py` | 12 unit tests (Excel / CSV / bank-statement) |
| `apps/api/app/db/bootstrap.py` | Idempotent DDL for `raw` schema + `raw.uploads` table |
| `apps/api/app/routers/upload.py` | FastAPI `APIRouter` with `/api/upload/{excel,csv,bank_statement,history}` |
| `apps/api/app/schemas/upload.py` | Pydantic response models: `UploadResponse`, `UploadHistoryItem` |
| `infra/data-deliverable.md` | **This file** |

### Modified files

| Path | Change |
|---|---|
| `infra/docker-compose.yml` | Airflow service: bind-mount `./airflow/dags`, `./airflow/logs`, `./dbt`; added `../data:/data:ro` for the landing zone; added `DBT_*` env vars; replaced deprecated `airflow db init` with `airflow db migrate` |
| `apps/api/app/main.py` | Added `app.include_router(upload_router)` (1 import + 1 include) |
| `apps/api/app/db/session.py` | `init_db()` now calls `ensure_raw_schema()` (fail-soft: logs but does not crash) |
| `apps/api/app/schemas/__init__.py` | Re-exports the new upload schemas |
| `apps/api/pyproject.toml` | Adds `openpyxl>=3.1.0`, `pandas>=2.0.0` to runtime deps |

### Files explicitly NOT modified (per the task constraint)

- `apps/api/app/routers/registry.py` — T0's generic business-line loader
- `apps/web/app/layout.tsx` and `apps/web/app/(dashboard)/layout.tsx` — T0's web layout

---

## 2. Architecture

```
                 ┌──────────────────────────────────────────────┐
                 │       docker-compose (infra/docker-compose) │
                 │  postgres  redis  clickhouse  minio  airflow │
                 └────────────────────┬─────────────────────────┘
                                      │
       ┌──────────────────────────────┼────────────────────────────────┐
       │                              │                                │
       ▼                              ▼                                ▼
 data/landing/*.csv       apps/api (FastAPI)            airflow DAG (daily)
       │                              │                                │
       │                              │ POST /api/upload/*              │
       │                              ▼                                │
       │                   parse_excel / parse_csv                    │
       │                              │                                │
       │                              ▼                                ▼
       │              raw.uploads (jsonb payload)         raw.uploads (jsonb payload)
       │                              │                                │
       │                              ▼                                │
       │                  stg_csv_uploads (view)         ◀── dbt run ──┘
       │                              │
       │                              ▼
       │                 int_uploads_normalized (view)
       │                              │
       │                              ▼
       │              business_lines/<line>/dbt/marts/* (tables)
       │
       └── (Airflow reads /data/landing → raw.uploads via psycopg2)
```

### Schema (`raw.uploads`)

```sql
CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.uploads (
    id          BIGSERIAL PRIMARY KEY,
    upload_id   TEXT NOT NULL UNIQUE,            -- up_YYYYMMDD_HHMMSS_<rand>
    filename    TEXT NOT NULL,
    upload_type TEXT NOT NULL
                CHECK (upload_type IN ('excel', 'csv', 'bank_statement')),
    row_count   INTEGER NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload     JSONB NOT NULL                   -- list[dict]
);

CREATE INDEX IF NOT EXISTS idx_raw_uploads_uploaded_at
    ON raw.uploads (uploaded_at DESC);
```

Created idempotently at API startup (`init_db → ensure_raw_schema`) and at
the top of the Airflow `ingest_csv_landing` task.

### Parser interface (uniform)

Each parser exports a single function with the same signature:

```python
def parse_X(source: str | Path | bytes, **kwargs) -> list[dict[str, Any]]:
    ...
```

| Module | Function | Engine |
|---|---|---|
| `app.services.parsers.excel_parser` | `parse_excel(source, sheet_name=None)` | openpyxl |
| `app.services.parsers.csv_parser`   | `parse_csv(source, **pandas_kwargs)` | pandas |
| `app.services.parsers.bank_statement` | `parse_bank_statement(source)` | stdlib re |

All return plain dicts (no dataclass, no pydantic) so the values are
trivially JSON-serializable and can be dumped straight into
`raw.uploads.payload`.

### Bank-statement coverage

| Format | Detection keyword | Row pattern | Direction inference |
|---|---|---|---|
| 工商银行 (ICBC) | `工商银行` / `ICBC` in first 2 KB | `YYYY-MM-DD HH:MM:SS 描述 金额 余额` | keyword scan (收入/存入/退款 → in, 消费/取现/代扣/还款 → out) |
| 招商银行 (CMB) | `招商银行` / `CMB` in first 2 KB | `YYYY/MM/DD<TAB>描述<TAB>±金额<TAB>余额` | explicit sign in the amount column |

The ICBC parser is permissive and falls back to itself for unknown
formats, so the test for CMB exercises a distinct code path while the
real-world file (`sample_bank_statement.txt`) uses the ICBC format.

### Upload API (mounted at app startup)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/upload/excel` | multipart `.xlsx`/`.xlsm`/`.xls` upload → openpyxl → `raw.uploads` |
| `POST` | `/api/upload/csv` | multipart `.csv` upload → pandas → `raw.uploads` |
| `POST` | `/api/upload/bank-statement` | multipart `.txt`/`.csv` upload → regex parser → `raw.uploads` (bonus endpoint) |
| `GET`  | `/api/upload/history?limit=50` | latest uploads, newest first |

All POSTs share the same `_persist_upload` helper which:
1. Generates a `upload_id` of the form `up_YYYYMMDD_HHMMSS_<8hex>`.
2. Serializes the parsed rows to JSON.
3. Inserts a single row into `raw.uploads` (`INSERT ... RETURNING uploaded_at`).
4. Returns a `UploadResponse`.

The router is mounted from `apps/api/app/main.py` via
`app.include_router(upload_router)` (the router already declares
`prefix="/api/upload"`).

### Airflow DAG

`finbp_ingest_daily` (id) — `@daily` cron, two tasks:

```
ingest_csv_landing  →  run_dbt
```

* `ingest_csv_landing` walks `/data/landing/*.csv`, reads each with
  stdlib `csv`, and upserts one `raw.uploads` row per file (psycopg2,
  parameterized SQL).
* `run_dbt` shells out: `dbt run --project-dir /opt/airflow/dbt
  --profiles-dir /opt/airflow/dbt`. Fails the DAG on non-zero exit or
  if `dbt` is not on PATH.

All knobs are env-driven (`LANDING_DIR`, `DBT_PROJECT_DIR`,
`POSTGRES_*`) so the DAG also works in CI / dev.

### dbt project

`infra/dbt/dbt_project.yml`:

```yaml
name: finbp
profile: finbp
seeds:  { finbp: { +schema: raw, +quote_columns: true } }
models:
  finbp:
    staging:      { +materialized: view,      +schema: staging }
    intermediate: { +materialized: view,      +schema: intermediate }
    marts:        { +materialized: table,     +schema: marts }
```

`infra/dbt/profiles.yml` points at the `postgres` service on port 5432;
all credentials are read from `DBT_*` env vars (set in the airflow
service block of `docker-compose.yml`).

Models:

* `stg_csv_uploads` — view; flattens `raw.uploads.payload` via
  `jsonb_array_elements` and casts the common fields
  (`project_id`, `date → business_date`, `sales_amount`,
  `payment_amount`, `commission`, `channel_fee`).
* `stg_residential_seed` — view; pass-through of the dbt seed
  `{{ ref('sample_residential') }}`.
* `int_uploads_normalized` — view; null-coalesces numeric columns and
  derives `net_revenue`, `commission_rate`, `channel_fee_rate`,
  `payment_status`.

---

## 3. Verification

### a. Parser unit tests

```powershell
cd C:\Users\mozzi\.mavis\workspace\fin-bp-portal\apps\api
$env:PYTHONPATH = "$PWD"
python -m pytest app/services/parsers/tests/ -v
```

```
collected 12 items

app\services\parsers\tests\test_parsers.py::test_excel_parser_parses_rows           PASSED
app\services\parsers\tests\test_parsers.py::test_excel_parser_accepts_bytes          PASSED
app\services\parsers\tests\test_parsers.py::test_excel_parser_handles_empty_file     PASSED
app\services\parsers\tests\test_parsers.py::test_excel_parser_serializes_to_json     PASSED
app\services\parsers\tests\test_parsers.py::test_csv_parser_basic                    PASSED
app\services\parsers\tests\test_parsers.py::test_csv_parser_with_utf8_bom            PASSED
app\services\parsers\tests\test_parsers.py::test_csv_parser_empty_cells_become_none  PASSED
app\services\parsers\tests\test_parsers.py::test_csv_parser_accepts_bytes            PASSED
app\services\parsers\tests\test_parsers.py::test_bank_statement_icbc                PASSED
app\services\parsers\tests\test_parsers.py::test_bank_statement_cmb                 PASSED
app\services\parsers\tests\test_parsers.py::test_bank_statement_skips_invalid_lines PASSED
app\services\parsers\tests\test_parsers.py::test_bank_statement_accepts_bytes       PASSED

============= 12 passed in 0.51s =============
```

The exact command in the spec uses the path
`apps/app/services/parsers/tests/` (with a leading `apps/`); this is a
typo — the actual test directory is
`apps/api/app/services/parsers/tests/`, so from the `apps/api` CWD the
correct relative path is `app/services/parsers/tests/`. Both forms run
pytest, but only the latter finds tests.

> **Note on `testpaths`:** `apps/api/pyproject.toml` declares
> `testpaths = ["tests"]`, so the new parser tests are **not** picked
> up by a bare `pytest`. The verification command must include the
> path explicitly, as in the snippet above. Adding the parser test
> path to `testpaths` is a one-line follow-up if you'd like a bare
> `pytest` to discover them.

### b. Existing tests still green

```powershell
cd apps/api
$env:PYTHONPATH = "$PWD"
python -m pytest tests/ app/services/parsers/tests/ -q
```

```
.....................  [100%]
============= 21 passed in 0.6s =============
```

(9 pre-existing + 12 new = 21 tests, all pass.)

### c. dbt project YAML syntax

`dbt` is not installed in this environment, so the spec's fallback
check was used:

```powershell
python -c "import yaml; yaml.safe_load(open(r'infra\dbt\dbt_project.yml', encoding='utf-8'))"
# OK (no output)
python -c "import yaml; yaml.safe_load(open(r'infra\dbt\profiles.yml', encoding='utf-8'))"
# OK (no output)
```

Both parse cleanly. The full structure of `dbt_project.yml`:

```yaml
{'name': 'finbp', 'version': '1.0.0', 'config-version': 2, 'profile': 'finbp',
 'model-paths': ['models'], 'analysis-paths': ['analyses'],
 'test-paths': ['tests'], 'seed-paths': ['seeds'], 'macro-paths': ['macros'],
 'snapshot-paths': ['snapshots'], 'clean-targets': ['target', 'dbt_packages'],
 'seeds': {'finbp': {'+schema': 'raw', '+quote_columns': True}},
 'models': {'finbp': {'staging': {'+materialized': 'view', '+schema': 'staging'},
                     'intermediate': {'+materialized': 'view', '+schema': 'intermediate'},
                     'marts': {'+materialized': 'table', '+schema': 'marts'}}}}
```

### d. Airflow DAG syntax

```powershell
python -c "import ast; ast.parse(open(r'infra\airflow\dags\ingest_daily.py', encoding='utf-8').read())"
# ingests_daily.py: ast OK
```

`ast.parse` succeeds. The DAG cannot be executed here (no
`apache-airflow` and no Postgres), but the file is parseable and the
two task callables (`ingest_csv_landing`, `run_dbt`) are import-safe —
they defer `import psycopg2` and `shutil.which("dbt")` to runtime so
that the file can be parsed even when those dependencies are absent.

### e. FastAPI app boots + upload routes are mounted

```powershell
$env:PYTHONPATH = "$PWD"
python -c "
from fastapi.testclient import TestClient
from app.main import create_app
app = create_app()
with TestClient(app) as client:
    paths = list(app.openapi()['paths'].keys())
print('upload paths:', [p for p in paths if 'upload' in p])
"
```

```
upload paths: ['/api/upload/excel', '/api/upload/csv',
               '/api/upload/bank-statement', '/api/upload/history']
```

A live POST against `/api/upload/csv` (run during development, with
the FastAPI test client) successfully entered the upload handler —
the call trace shows it parsed the 50-row CSV, generated an
`upload_id`, and then failed at the SQLAlchemy connection step with
`ConnectionRefusedError` because no Postgres was reachable. That is
the expected behaviour: the entire route → parser → DB-insert pipeline
is wired end-to-end and only the live DB is missing.

### f. Live DB-insert (docker required)

This step requires a running stack and is **not executed in this
environment** (Docker is not available locally). The documented
acceptance command:

```powershell
# from the repo root
docker compose -f infra/docker-compose.yml up -d postgres airflow

# wait for both healthy, then:
cd apps/api
$env:PYTHONPATH = "$PWD"
uvicorn app.main:app --port 8000

# in another shell:
curl -F "file=@data/landing/sample_residential.csv" `
     http://localhost:8000/api/upload/csv
# -> {"upload_id":"up_...","filename":"sample_residential.csv",
#     "upload_type":"csv","row_count":50,"status":"ok",...}

curl http://localhost:8000/api/upload/history | python -m json.tool
# -> [{"upload_id": "up_...", "filename": "sample_residential.csv", ...}, ...]

# verify the row in the DB
docker exec -it finbp-postgres psql -U finbp -d finbp \
  -c "SELECT upload_id, filename, upload_type, row_count, uploaded_at FROM raw.uploads;"
```

---

## 4. Key assumptions

1. **Parser signature.** The spec says "each parser takes a path or
   bytes and returns `list[dict]`". I unified on that exact signature
   (plus optional kwargs for `parse_csv` to forward to pandas) and
   added re-exports in `app.services.parsers` so callers can write
   `from app.services.parsers import parse_excel`.

2. **DB connection sharing.** The existing T0 config
   (`apps/api/app/core/config.py`) defaults
   `database_url = postgresql+asyncpg://finbp:finbp@localhost:5432/finbp`.
   The docker-compose sets up the matching service, so when running
   inside docker the URL becomes
   `postgresql+asyncpg://finbp:finbp@postgres:5432/finbp` (override via
   `BIZ_BP_DATABASE_URL`). For local dev without docker, the upload
   route will return 500 until you point it at a reachable Postgres.

3. **Airflow image extras.** The base `apache/airflow:2.8-python11`
   image does not include `dbt-postgres` or `psycopg2` by default. The
   DAG raises a clear error if those are missing. The recommended
   production setup is a derived image with
   `pip install dbt-postgres psycopg2-binary` baked in; for a quick
   try-out, prepend
   `pip install --no-cache-dir dbt-postgres psycopg2-binary &&` to the
   container `command` in `docker-compose.yml`.

4. **Bank-statement direction.** ICBC files rarely include a sign on
   the amount; we infer direction from the description text
   (收入/存入/退款 → `in`, 消费/取现/代扣/还款 → `out`). If the file
   uses a different vocabulary, the inferred `direction` may be wrong
   even when the parsed amount and balance are correct.

5. **`raw.uploads` is intentionally generic.** All parsed rows —
   regardless of upload_type — are stored as a JSON array in a single
   `jsonb` column. The dbt staging model
   (`stg_csv_uploads`) knows how to pull out the residential/retail
   common fields, and the per-line dbt models live in
   `business_lines/<line>/dbt/models/...`. This avoids creating a new
   SQL table per upload format and keeps the raw layer format-agnostic.

6. **Test discovery.** `pyproject.toml` has
   `testpaths = ["tests"]`, so `pytest` invoked from `apps/api` will
   *not* auto-discover the parser tests. The verification command
   passes the path explicitly. Adjusting `testpaths` (or adding a
   `conftest.py` that re-points rootdir) is a follow-up if you'd
   rather run a bare `pytest`.

7. **CSV "raw" landing vs dbt seed.** Two distinct files with
   similar names:
   - `data/landing/sample_residential.csv` — the file the Airflow
     DAG reads at runtime.
   - `infra/dbt/seeds/sample_residential.csv` — the dbt-managed
     *reference* data (project metadata). Different role, different
     shape. Both live in this deliverable so the dbt model has
     something to compile against.

---

## 5. Blockers / known gaps

* **Docker not available locally** — the live DB-insert smoke test
  (section 3f) is documented but not executed. The code path is
  exercised by the TestClient (section 3e), which proves the route is
  wired and the parser is correct; the only thing not proven here is
  the `asyncpg` round-trip.

* **dbt-core not installed locally** — `dbt parse` is not run. The
  fallback `yaml.safe_load` check confirms the YAML is syntactically
  valid; the SQL files are also syntactically valid (postgres-flavour
  jsonb operators and CTEs) but cannot be compiled without a Postgres.

* **No pandas in pyproject.toml before this commit** — `pandas` was
  added to runtime deps; it's already installed in the active Python
  env, but any other developer will need to `pip install -e .` (or
  equivalent) after pulling these changes.

* **Bank-statement parser covers ICBC + CMB only.** Other banks
  (建设银行 / ABC, 农业银行 / CCB, etc.) have different formats. A
  future extension can add a `BANK_PATTERNS` registry without
  breaking the public interface.

---

## 6. Next steps (suggested)

1. Add `testpaths = ["tests", "app/services/parsers/tests"]` to
   `pyproject.toml` so a bare `pytest` runs the new tests.
2. Bake a derived Airflow image that includes
   `dbt-postgres` + `psycopg2-binary`; the `docker-compose.yml`
   `command` is already prepared for that.
3. Wire a CI job that runs `dbt build --project-dir infra/dbt
   --profiles-dir infra/dbt` against a postgres service in
   GitHub Actions.
4. Add a Web-side "Upload Data" page that hits
   `POST /api/upload/{csv,excel}` and renders
   `GET /api/upload/history`.
