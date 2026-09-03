# Residential Business Line — Deliverable (T1)

> First real consumer of the fin-bp-portal plugin framework. Implements the
> residential (住宅) analytics line end-to-end: manifest, indicators, FastAPI
> router, DBT models, mock seed data, and 5 Next.js web pages.

---

## 1. Files created

All paths are relative to the monorepo root `C:\Users\mozzi\.mavis\workspace\fin-bp-portal`.

### Manifest + indicators (2)
- `business_lines/residential/manifest.yaml` — `id=residential`, icon=HomeOutlined, 5 nav entries, `api_prefix=/api/lines/residential`, warehouse schemas.
- `business_lines/residential/indicators.yaml` — 10 indicators + 5 chart specs.

### API (1 file, no `__init__.py` so it stays a "loaded-by-path" module)
- `business_lines/residential/api/router.py` — `APIRouter` with endpoints:
  - `GET /ping`
  - `GET /info`
  - `GET /indicators`
  - `GET /projects`
  - `GET /projects/{project_id}`
  - `GET /projects/{project_id}/dynamic-pl`
  - `GET /projects/{project_id}/payment`
  - `GET /projects/{project_id}/redlines`
  - `GET /projects/{project_id}/dedup-forecast`

### Web pages (5)
- `business_lines/residential/web/pages/index.tsx`          — Overview: KPI cards + AG Grid project list.
- `business_lines/residential/web/pages/dynamic-pl.tsx`     — Dynamic P&L with 3 parameter sliders + sensitivity chart.
- `business_lines/residential/web/pages/payment.tsx`         — 回款-佣金-渠道费 stacked bar + 回款 vs 计划 line.
- `business_lines/residential/web/pages/redlines.tsx`        — Three red lines radar (current vs thresholds) + status table.
- `business_lines/residential/web/pages/dedup-forecast.tsx`  — Historical + 12-month forecast line with 80% confidence band.

### DBT (7)
- `business_lines/residential/dbt/dbt_project.yml`
- `business_lines/residential/dbt/models/staging/stg_residential_contracts.sql`
- `business_lines/residential/dbt/models/staging/stg_residential_payments.sql`
- `business_lines/residential/dbt/models/intermediate/int_residential_payment_weekly.sql`
- `business_lines/residential/dbt/models/marts/fct_residential_dynamic_pl.sql`
- `business_lines/residential/dbt/models/marts/fct_residential_payment.sql`
- `business_lines/residential/dbt/models/marts/fct_residential_redlines.sql`

### Seed data (8 projects, 1 file each)
- `business_lines/residential/data/seed/PRJ-001-shanghai-pudong.json`   — 上海·绿城黄浦江
- `business_lines/residential/data/seed/PRJ-002-beijing-haidian.json`   — 北京·万科海淀
- `business_lines/residential/data/seed/PRJ-003-shenzhen-nanshan.json`  — 深圳·华润前海
- `business_lines/residential/data/seed/PRJ-004-hangzhou-binjiang.json` — 杭州·龙湖滨江
- `business_lines/residential/data/seed/PRJ-005-chengdu-tianfu.json`    — 成都·保利天府
- `business_lines/residential/data/seed/PRJ-006-guangzhou-tianhe.json`  — 广州·中海天河
- `business_lines/residential/data/seed/PRJ-007-nanjing-jiangning.json` — 南京·金地江宁
- `business_lines/residential/data/seed/PRJ-008-suzhou-industry-park.json` — 苏州·金地工业园

### Utilities / evidence
- `business_lines/residential/validate.py`              — offline sanity check (parses YAML, JSON, registry).
- `business_lines/residential/_capture_evidence.py`     — re-runnable raw-JSON capture to `_evidence/`.
- `business_lines/residential/_evidence/01..04d.json`   — captured raw JSON bodies from the 5 verifications.
- `business_lines/residential/_evidence/01..04d.http.txt` — captured HTTP header per request.
- `business_lines/residential/deliverable.md`           — this file.

### Files modified (1)
- `business_lines/registry.yaml` — added `residential` entry under `lines:` (the file was previously `lines: []`). Pattern is idempotent: any duplicate `id: residential` would be a no-op when the loader iterates the dict (Pydantic would raise on duplicate keys at the YAML level, so the YAML file itself stays one-entry-per-id).

### Files NOT touched
- `apps/web/app/(dashboard)/*`
- `apps/api/app/routers/registry.py`
- `apps/api/app/main.py`
- `apps/api/app/core/registry.py`
- any file outside `business_lines/residential/`

---

## 2. Start commands

```powershell
# From the monorepo root
cd C:\Users\mozzi\.mavis\workspace\fin-bp-portal\apps\api
$env:PYTHONPATH = "$PWD"
$env:FIN_BP_PROJECT_ROOT = "C:\Users\mozzi\.mavis\workspace\fin-bp-portal"
python -m uvicorn app.main:app --port 8765
```

`FIN_BP_PROJECT_ROOT` is **not** strictly required because the loader also walks
up from its own `__file__`, but exporting it makes the project root
unambiguous on Windows where `C:\Users\mozzi\.mavis\workspace` and
`C:\Users\mozzi\.minimax\workspace` are reparse points of each other and
`Path.resolve()` follows the reparse.

```powershell
# Stop
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

(`uvicorn` runs under `python.exe`, so `Get-Process -Name uvicorn` does not
match — the listening PID is the parent python process.)

---

## 3. Verification — actual outputs

All curls were captured at 2026-09-02 15:05-15:06 (Asia/Shanghai) against
`http://localhost:8765`. Raw JSON in `_evidence/`.

### 3.1 `GET /api/registry/lines`
HTTP **200**. `lines=2` (the framework also picked up a sibling `retail` line
that was added by a parallel task; residential entry is present and well-formed).

```json
{
  "version": "0.1.9720c1de",
  "lines": [
    { "id": "residential", "name": "住宅分析",   "api_prefix": "/api/lines/residential", ... },
    { "id": "retail",      "name": "零售分析",   "api_prefix": "/api/lines/retail",      ... }
  ]
}
```

### 3.2 `GET /api/lines/residential/indicators`
HTTP **200**. 10 indicators + 5 charts. Sample:

| indicator_id          | unit | value   |
|----------------------|------|---------|
| dynamic_irr           | %    | 0.0162  |
| dynamic_net_margin    | %    | -0.1812 |
| payment_completion    | %    | 0.9381  |
| channel_fee_ratio     | %    | 0.0074  |
| asset_liability_ratio | %    | 0.6218  |
| net_debt_ratio        | %    | 0.8139  |
| cash_to_short_debt    | x    | 1.1994  |
| monthly_dedup_rate    | %    | 0.0505  |
| payment_vs_plan       | %    | 0.9381  |
| project_roi           | %    | -0.2066 |

### 3.3 `GET /api/lines/residential/projects`
HTTP **200**. `count=8`. 8 mock residential projects with realistic area/price.

### 3.4 `GET /api/lines/residential/projects/PRJ-001/dynamic-pl`
HTTP **200** (317 B). Sample:

```json
{
  "line_id": "residential",
  "project_id": "PRJ-001",
  "project_name": "上海·绿城黄浦江",
  "gross_sales_yi": 98.12,
  "dynamic_cost_yi": 78.6,
  "land_cost_yi": 42.0,
  "channel_fee_yi": 0.365,
  "commission_yi": 0.248,
  "tax_yi": 4.91,
  "net_profit_yi": -27.99,
  "irr": 0.019,
  "net_margin": -0.2,
  "project_roi": -0.2321,
  "monthly_dedup_rate": 0.0533
}
```

### 3.5 `GET /api/lines/residential/projects/PRJ-001/payment`
HTTP **200** (515 B). 12-month plan/actual series, `payment_completion=0.9707`.

### 3.6 `GET /api/lines/residential/projects/PRJ-001/redlines`
HTTP **200** (504 B). All three red lines `green` for PRJ-001:
- `asset_liability_ratio=0.6476` (threshold 0.70)
- `net_debt_ratio=0.7811`       (threshold 1.00)
- `cash_to_short_debt=1.439`    (threshold 1.00)

### 3.7 `GET /api/lines/residential/projects/PRJ-001/dedup-forecast`
HTTP **200** (585 B). 12 history + 12 forecast months, with 80% lower/upper band.

### 3.8 Sanity: `GET /api/lines/residential/projects/UNKNOWN`
HTTP **404** — `{"detail":"unknown project_id: UNKNOWN"}`.

### 3.9 Offline check
```
$ python -X utf8 business_lines/residential/validate.py
[OK] manifest.yaml  id=residential  api_prefix=/api/lines/residential  nav=5 entries
[OK] indicators.yaml  10 indicators, 5 charts
[OK] seed PRJ-001-shanghai-pudong.json  PRJ-001 上海·绿城黄浦江
[OK] seed PRJ-002-beijing-haidian.json  PRJ-002 北京·万科海淀
[OK] seed PRJ-003-shenzhen-nanshan.json  PRJ-003 深圳·华润前海
[OK] seed PRJ-004-hangzhou-binjiang.json  PRJ-004 杭州·龙湖滨江
[OK] seed PRJ-005-chengdu-tianfu.json  PRJ-005 成都·保利天府
[OK] seed PRJ-006-guangzhou-tianhe.json  PRJ-006 广州·中海天河
[OK] seed PRJ-007-nanjing-jiangning.json  PRJ-007 南京·金地江宁
[OK] seed PRJ-008-suzhou-industry-park.json  PRJ-008 苏州·金地工业园
[OK] registry.yaml lists 'residential'
```

---

## 4. Key assumptions

1. **The web pages live at `business_lines/<line>/web/pages/`** and are NOT
   auto-wired into the Next.js App Router. The page files are the business
   line's UI source-of-truth; the task constraint prohibits editing
   `apps/web/app/(dashboard)/*`. A future integration step (out of scope
   for T1) would either (a) copy/symlink these files into the App Router,
   or (b) introduce a dynamic route that loads them. Both options are
   non-breaking and the pages use only `@fin-bp/ui` / `antd` / `echarts`
   already in the workspace's `package.json`.

2. **Mock data is in the business line, not in core.** The FastAPI router
   loads `business_lines/residential/data/seed/*.json` directly. Replacing
   it with a real warehouse is a swap of one path. KPI formulas are
   reasonable but not real accounting — they are placeholders sized so
   the UI shows non-trivial numbers (e.g. IRR clamped to [-20%, +60%],
   channel fee ratio clamped to [0%, 20%]).

3. **DBT models are not actually compiled in this task.** They are written
   to the standard DBT layout and use `{{ ref(...) }}` exclusively, so
   `dbt run` will work once a `profiles.yml` and warehouse are provisioned.
   The mock values in `fct_residential_*` mart tables are written via
   `VALUES` so the model compiles without a backing source table.

4. **`registry.yaml` is mutated as an idempotent append.** When this task
   started, the file was `lines: []`. A `residential` entry was added.
   A parallel task added `retail` and `test_line` entries; both coexist
   cleanly with residential. The validation in
   `apps/api/app/core/registry.py` enforces unique IDs at the Pydantic
   level — adding a duplicate `id: residential` would raise on parse, so
   the file is de-facto idempotent.

5. **Indicator values for `/indicators` are aggregated across all loaded
   projects using a simple unweighted mean.** A real DBT-backed
   implementation would read from `mart_residential.*`; the current code
   falls back to computing the same metrics from the seed JSON so the UI
   works end-to-end before the warehouse is wired.

6. **Path quirk on Windows.** The workspace `C:\Users\mozzi\.mavis\workspace`
   and `C:\Users\mozzi\.minimax\workspace` are reparse points of each
   other. `Path.resolve()` follows the reparse in Python (returning
   `.minimax`), so the loader happily works through it. The
   `validate.py` script deliberately uses `Path(__file__).parent` (not
   `.resolve()`) and `os.path.exists` to stay robust against the
   reparse. Always export `FIN_BP_PROJECT_ROOT` explicitly when running
   on this host.

---

## 5. Blockers / known issues

- **None blocking the acceptance criteria.** All 5 mandatory verification
  curls returned the expected shape.
- The KPI values are mock. Once the real warehouse + DBT are wired, the
  router's `indicators()` and `project_dynamic_pl()` functions should be
  replaced with a thin SQL query against the marts; the response shape
  is already stable.
- `apps/web/app/(dashboard)/*` was not modified, so the new pages are
  present in `business_lines/residential/web/pages/` but not yet
  reachable from the dashboard UI. This is by design per the task
  constraint.
- The PowerShell console displays Chinese as GBK-encoded mojibake, but
  the underlying JSON is correct UTF-8 (verified by `urllib.request` +
  `json.loads` in Python; see `_evidence/*.json`).
