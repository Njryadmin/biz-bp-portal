# Plugin how-to — adding a new business line

> Audience: developers adding a new business line to the Fin BP Portal.
> Time to complete: **< 10 minutes** for the happy path.
> Audience reminder: **all line-specific code lives under `business_lines/<line_id>/`**.
> The rest of the monorepo (`apps/`, `packages/`, `infra/`) is generic and must
> never import a specific line.

---

## 0. What you are about to do (overview)

```
+------------------------------------------------------------------+
|  business_lines/registry.yaml   <- the ONE place you add the id  |
|                                                                  |
|     +-- business_lines/<line_id>/                                |
|     |   |- manifest.yaml        (line metadata, nav, api_prefix)|
|     |   |- indicators.yaml      (KPIs + chart definitions)       |
|     |   |- api/router.py        (FastAPI endpoints)              |
|     |   |- web/pages/*.tsx      (Next.js pages)                  |
|     |   |- dbt/models/*.sql     (warehouse models)               |
|     |   \- data/seed/                                             |
|     |                                                            |
|     +-- apps/api auto-discovers api/router.py via importlib     |
|     +-- apps/web renders nav, dashboard cards from the registry |
+------------------------------------------------------------------+
```

You only edit **two** files outside `business_lines/<line_id>/`:
1. The new directory `business_lines/<line_id>/` (copy from `_template`).
2. The single line appended to `business_lines/registry.yaml`.

Everything else — left-nav, dashboard cards, API mount — is auto-discovered.

---

## 1. Copy the template

```bash
# From the monorepo root
cp -r business_lines/_template business_lines/<line_id>
# <line_id> MUST be URL-safe: [a-z0-9_-]+. e.g. consumer_loan, wealth_mgmt.
```

What you get inside the new directory:

```
business_lines/<line_id>/
|-- manifest.yaml          (rename from manifest.yaml.example and edit)
|-- indicators.yaml        (rename from indicators.yaml.example and edit)
|-- api/router.py          (rename from router.py.example, replace `change-me`)
|-- web/pages/index.tsx    (your page; copy _example.tsx and edit)
|-- dbt/dbt_project.yml    (copy dbt_project.yml.example)
|-- dbt/models/*.sql       (one or more dbt models)
\-- data/seed/             (optional CSVs / seed data)
```

## 2. Edit the two YAML files

### `business_lines/<line_id>/manifest.yaml`

```yaml
id: <line_id>                     # MUST equal the directory name
name: "<Human readable name>"
version: 0.1.0
description: "One-paragraph business summary."
owner: "you@example.com"
icon: "BankOutlined"              # any @ant-design/icons name (CamelCase + Outlined)
nav:
  - path: "/<line_id>"            # appears in the cockpit's left nav
    title: "Overview"
  - path: "/<line_id>/trends"
    title: "Trends"
api_prefix: "/api/lines/<line_id>"   # MUST start with /
warehouse:
  schema: "raw_<line_id>"
  dbt_schema: "stg_<line_id>"
  mart_schema: "mart_<line_id>"
refresh:
  schedule: "0 2 * * *"
  enabled: true
features:
  universal_kpi: true
  universal_chart: true
  ag_grid: true
```

### `business_lines/<line_id>/indicators.yaml`

```yaml
indicators:
  - id: gmv_daily
    title: "Daily GMV"
    unit: "CNY"
    format: currency            # currency | number | percent | ratio
    aggregation: sum
    source: "mart_<line_id>.daily_gmv"

charts:
  - id: gmv_trend
    title: "GMV Trend"
    type: line                  # line | bar | pie | area
    x: date
    y: [gmv_daily]
    source: "mart_<line_id>.daily_gmv"
```

Tip: the `id` field on each indicator is what your `api/router.py` returns
from `/<line_id>/kpi`. The cockpit never hard-codes indicator ids — it just
renders whatever the manifest and your router return.

## 3. Wire the API and (optionally) the page

### `business_lines/<line_id>/api/router.py`

```python
from fastapi import APIRouter

router = APIRouter()

@router.get("/ping")
async def ping() -> dict:
    return {"status": "ok", "line": "<line_id>"}

@router.get("/kpi")
async def kpi() -> dict:
    # Return shape matches packages/types KpiResponse.
    return {
        "line_id": "<line_id>",
        "items": [
            {"indicator_id": "gmv_daily", "value": 12345.6, "unit": "CNY"},
        ],
    }
```

The loader (`apps/api/app/routers/registry.py`) imports this file via
`importlib` and mounts it under `api_prefix`. **You do not register the
router anywhere by hand** — the registry.yaml entry is enough.

### `business_lines/<line_id>/web/pages/index.tsx` (optional)

```tsx
import { UniversalKpiCard, UniversalChart, EmptyState } from "@fin-bp/ui";

export default function LineOverview() {
  return (
    <div style={{ padding: 24 }}>
      <h1>Line overview</h1>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16 }}>
        <UniversalKpiCard
          indicator={{ id: "gmv_daily", name: "Daily GMV", unit: "CNY", format: "currency" }}
          value={12345.6}
          delta={0.12}
          sparkline={[10, 12, 9, 14, 18, 17, 22]}
        />
      </div>
      <UniversalChart
        type="line"
        data={{ categories: ["Mon", "Tue", "Wed", "Thu", "Fri"], values: [10, 12, 9, 14, 18] }}
        options={{ title: "GMV Trend" }}
        style={{ height: 320, marginTop: 24 }}
      />
    </div>
  );
}
```

Pages are auto-discovered by Next.js' file-based routing. The path you
declared in `manifest.yaml.nav[].path` is the URL the user sees.

## 4. Register the line

Append the new id to the single registry file. **This is the only edit
outside the `business_lines/<line_id>/` directory.**

```yaml
# business_lines/registry.yaml
lines:
  - id: <line_id>
    manifest: business_lines/<line_id>/manifest.yaml
```

Two valid forms are accepted:

```yaml
lines:
  - id: <line_id>                              # form A (preferred)
    manifest: business_lines/<line_id>/manifest.yaml
  # OR form B (manifest path defaults to the id)
  - id: <line_id>
```

## 5. Restart and verify

```bash
# API
cd apps/api
uvicorn app.main:app --reload --port 8000

# In another shell: confirm the new line is registered
curl -s http://localhost:8000/api/registry/lines | python -m json.tool

# You should see your line in the `lines` array, with `display_name`,
# `indicators_count`, `icon`, `nav`, and `api_prefix` filled in.

# Web
cd apps/web
npm run dev
# Open http://localhost:3000 — your line appears in the left nav and on
# the Overview dashboard cards.
```

If the API fails to import your router, the line still shows up in
`/api/registry/lines` (so you can see it was discovered) but mounting
its API router is skipped and the line's `/__error__` endpoint returns
`500` with a helpful message. Check the API logs for the traceback.

---

## 6. 成功案例 — < 30 分钟新增一条业务线

2026-09-02 用第三条业务线 **`retail-leasing` (零售租赁与市场报告)**
作为可扩展性的活体检验。**全部交付在不到 30 分钟内完成**,核心代码
(`apps/`、`infra/`、`business_lines/_template/` 之外) 实际改动行数 ≤ 10 行,
且**未触动 T0/T1/T2/T3/T4 的任何已有交付物**。

| 改动位置 | 文件 | 行数 | 备注 |
|---------|------|------|------|
| 业务线目录 | `business_lines/retail-leasing/**` | 新建(21 个文件) | 仅落在自己的目录里 |
| 注册清单 | `business_lines/registry.yaml` | 追加 2 行(`- id: retail-leasing` + 1 行 `manifest: ...`) | 算在 ≤ 10 行预算内 |
| 文档 | `docs/plugin-howto.md` | 增加本节(纯文档,非代码) | 不计入 10 行预算 |

具体审计见 `business_lines/retail-leasing/deliverable.md` 的"核心代码改动审计"小节。

### 6.1 5 步走完的实际复盘

1. `Copy-Item -Recurse business_lines\_template business_lines\retail-leasing` →
   得到模板骨架(10 个文件)。
2. 填 `manifest.yaml`(填字段名,约 45 行)+ `indicators.yaml`(8 个 KPI + 5 个 chart) →
   完成业务线自描述。
3. 改 `api/router.py`(基于 `_template/api/router.py.example` 改 4 个 endpoint) →
   `/ping` + `/indicators` + `/properties` + `/market-benchmark` + `/vacancy-alerts` + `/properties/{id}`。
4. 写 `data/seed/properties.json`(5 个 mock 商铺 + 各自 3-4 个竞品) + 4 个 `.tsx` 页面 →
   概览/市场报告/空置预警/租赁 KPI。
5. 在 `business_lines/registry.yaml` 末尾追加 2 行 → **API 自动挂载,前端 nav 自动出现**。

### 6.2 与已有业务线的差异(证明框架真的解耦)

`retail-leasing` 故意引入了 T1(住宅)/T2(零售)**都没有的领域概念**,
用来证明框架不会反向渗透:

- **市场基准对标差 (`benchmark_gap_pct`)** — 实际成交 vs 同地段竞品中位数,
  这是租赁交易场景独有的指标,retail 关心的是 NOI/坪效,residential 关心的是 IRR。
- **业主空置期 (`owner_vacancy_days`)** — 业主视角,retail/residential 都是资管视角。
- **可比物业 (`comparables[]`)** — 单商铺挂 3-5 个竞品的列表,
  这是市场报告业务的核心数据,retail 关注的是 brand_mix,residential 关注的是户型。
- **季度市场报告计数 (`quarterly_market_reports`)** — 偏研究/出版业 KPI,
  与零售运营 KPI 完全异构。

### 6.3 验证命令(以 retail-leasing 为例)

```bash
# 启动 API
cd apps/api
$env:PYTHONPATH = "$PWD"
python -m uvicorn app.main:app --port 8767

# 另开终端
irm http://localhost:8767/api/registry/lines
irm http://localhost:8767/api/lines/retail-leasing/indicators
irm http://localhost:8767/api/lines/retail-leasing/properties
irm http://localhost:8767/api/lines/retail-leasing/market-benchmark
irm http://localhost:8767/api/lines/retail-leasing/vacancy-alerts
```

期望:
- `/api/registry/lines` → 包含 `retail-leasing`(以及 residential/retail),共 3 条
- `/indicators` → `count = 8`
- `/properties` → `count ≥ 3`(实际 5)
- `/market-benchmark` → 至少 1 个物业,每个含 3-4 个 comparables
- `/vacancy-alerts` → 至少 1 条高/中风险预警

> **结论**: 5 步走完,核心代码改动 ≤ 10 行,新业务线完全独立于 `apps/`/`infra/`/`packages/`。
> 这就是 T0 设计的"单点挂载、零侵入扩展"插件框架的胜利。

---

## Anatomy of the auto-rendered cockpit

```
+-----------------+   /api/registry/lines   +-----------------------+
| apps/web        | -----------------------> | apps/api              |
| (Next.js 14)    | <----------------------- | (FastAPI)             |
|                 | { version, lines: [...]} |                       |
+-----------------+                          +-----------------------+
       |                                                |
       v                                                v
 SidebarMenu groups by line          importlib loads each
 current line highlighted via         <line_id>/api/router.py
 usePathname(); sorted by             and mounts it at <api_prefix>
 display_name (zh-CN aware)
```

The cockpit never imports `business_lines/<line_id>/*`. Everything it
knows about a line comes from `/api/registry/lines` at render time.

---

## What you must NOT do

- Do **not** add a `<line_id>` string literal in `apps/*` or `packages/*`.
- Do **not** `import` from `business_lines/<line>/` anywhere outside
  that business line's own sub-tree.
- Do **not** edit `apps/api/app/routers/registry.py` to add a new line.
  That file is generic; it discovers lines from `registry.yaml`.
- Do **not** hard-code the line's `api_prefix` in your dashboard pages.
  Fetch `/api/registry/lines/<line_id>` if you need it.

## What you SHOULD do

- Keep all line-specific code under `business_lines/<line_id>/`.
- Use the universal components from `@fin-bp/ui` for KPI cards, charts
  and grids — they are designed to be line-agnostic.
- Treat `manifest.yaml` as the contract between the line author and the
  cockpit. Changes to field semantics are coordinated with the cockpit
  maintainer.
- Restart the API after editing `registry.yaml` (Next.js dev mode
  hot-reloads the web layer).
