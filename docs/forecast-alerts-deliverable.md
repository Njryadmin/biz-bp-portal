# Forecast & Alerts — Deliverable

> **Modules shipped:** Rolling Forecast Engine (Module 1) + Alert Center (Module 2)
> **Date:** 2026-09-02
> **Lines of code:** 2 engines, 2 routers, 2 BFF proxy trees, 2 pages, 6 YAML configs, 44 new tests, all green.
> **Status:** PASS

---

## 1 · File inventory

### Backend (Python · FastAPI)

| Path | Purpose |
|---|---|
| `apps/api/app/services/forecast_engine.py` | Universal rolling-forecast engine. Reads `business_lines/<line>/forecast.yaml`; supports `sma` / `ema` / `linear_trend` / `seasonal_naive`; returns historical + 12-month forecast with 95% CI, MAPE, bias, and optional attribution. |
| `apps/api/app/services/alert_engine.py` | Universal alert engine. Reads `business_lines/<line>/alerts.yaml`; supports `> < >= <= == between change_pct` operators plus `consecutive: N`; in-memory store; render templated messages. |
| `apps/api/app/routers/forecast.py` | Cross-line HTTP router mounted at `/api/forecast/*`. |
| `apps/api/app/routers/alerts.py` | Cross-line HTTP router mounted at `/api/alerts/*`. |
| `apps/api/app/main.py` | `app.include_router(forecast_router)` + `app.include_router(alerts_router)` (next to sensitivity/copilot). No registry change. |
| `apps/api/tests/test_forecast.py` | 20 tests — profile load, all 4 methods, MAPE/bias, attribution, HTTP, universality. |
| `apps/api/tests/test_alerts.py` | 24 tests — profile load, 6 operators, `consecutive`, summary, ack/delete, history pagination, HTTP, universality. |

### Backend config (YAML, per line)

| Path | Series / rules |
|---|---|
| `business_lines/residential/forecast.yaml` | 4 series (dynamic_irr, payment_completion, dedup_rate, channel_fee_ratio) + 4 attribution buckets |
| `business_lines/retail/forecast.yaml` | 4 series (noi, efficiency, collection_rate, vacancy_rate) + 4 attribution buckets |
| `business_lines/retail-leasing/forecast.yaml` | 4 series (occupancy_rate, avg_deal_rent, benchmark_gap_pct, renewal_rate) + 4 attribution buckets |
| `business_lines/residential/alerts.yaml` | **5 rules** (irr_below_threshold, payment_drop, redline_breach, dedup_stall, irr_between_band) + 4 attribution buckets |
| `business_lines/retail/alerts.yaml` | **5 rules** (noi_drop, collection_below, vacancy_spike, vacancy_consecutive_high, efficiency_below_band) + 4 attribution buckets |
| `business_lines/retail-leasing/alerts.yaml` | **5 rules** (occupancy_below, vacancy_days_high, renewal_drop, benchmark_gap_negative, renewal_consecutive_low) + 4 attribution buckets |

### Frontend (TypeScript · Next.js 14 / AntD 5 / ECharts 5)

| Path | Purpose |
|---|---|
| `apps/web/app/(dashboard)/forecast/page.tsx` | Forecast page — left param panel (line/indicator/method/horizon/include_attribution), right: line chart with 95% CI band, MAPE/bias/confidence stats, attribution table. |
| `apps/web/app/(dashboard)/alerts/page.tsx` | Alert Center — top bar (line selector + severity tabs + [立即检查]), triggered-alert cards with severity color bar + ack/ignore actions, rules list (collapsible). 10-second polling for in-app delivery. |
| `apps/web/app/(dashboard)/_components/Topbar.tsx` | Added 2 new links: 滚动预测 (`/forecast`) + 告警中心 (`/alerts`) with icons. |
| `apps/web/app/(dashboard)/[line]/page.tsx` | Added 2 new cross-cutting shortcut cards: 滚动预测 + 告警中心 (each with `?line=` pre-select). |
| `apps/web/app/api/forecast/profiles/route.ts` | BFF proxy — list profiles |
| `apps/web/app/api/forecast/profiles/[line_id]/route.ts` | BFF proxy — get one profile |
| `apps/web/app/api/forecast/run/route.ts` | BFF proxy — POST /run |
| `apps/web/app/api/forecast/compare/route.ts` | BFF proxy — POST /compare (actuals vs forecast variance) |
| `apps/web/app/api/alerts/profiles/route.ts` | BFF proxy — list alert profiles |
| `apps/web/app/api/alerts/rules/[line_id]/route.ts` | BFF proxy — list rules |
| `apps/web/app/api/alerts/rules/[line_id]/summary/route.ts` | BFF proxy — rule summary |
| `apps/web/app/api/alerts/check/route.ts` | BFF proxy — POST /check |
| `apps/web/app/api/alerts/history/route.ts` | BFF proxy — GET /history (with line_id, limit, offset) |
| `apps/web/app/api/alerts/acknowledge/[alert_id]/route.ts` | BFF proxy — POST /acknowledge |
| `apps/web/app/api/alerts/[alert_id]/route.ts` | BFF proxy — DELETE (soft delete) |

No new npm packages. `package.json` unchanged.

---

## 2 · 3-line forecast profile summary

| Line | Series | Methods used |
|---|---|---|
| residential | `dynamic_irr`, `payment_completion`, `dedup_rate`, `channel_fee_ratio` (4) | linear_trend, ema, sma, seasonal_naive |
| retail | `noi`, `efficiency`, `collection_rate`, `vacancy_rate` (4) | linear_trend, ema, sma, seasonal_naive |
| retail-leasing | `occupancy_rate`, `avg_deal_rent`, `benchmark_gap_pct`, `renewal_rate` (4) | ema, linear_trend, sma, seasonal_naive |

## 3 · 3-line alert rules summary

| Line | Rules (5 each) | Operators covered |
|---|---|---|
| residential | irr_below_threshold (`<`+consecutive), payment_drop (change_pct), redline_breach (`==`), dedup_stall (`<`+consecutive 2), irr_between_band (between) | `<` `change_pct` `==` `between` consecutive |
| retail | noi_drop (change_pct), collection_below (`<`), vacancy_spike (`>`), vacancy_consecutive_high (`>`+consecutive 3), efficiency_below_band (between) | `<` `>` `change_pct` `between` consecutive |
| retail-leasing | occupancy_below (`<`), vacancy_days_high (`>`), renewal_drop (change_pct), benchmark_gap_negative (`<`), renewal_consecutive_low (`<`+consecutive 2) | `<` `>` `change_pct` consecutive |

---

## 4 · Test results

### New tests

```text
apps/api/tests/test_forecast.py ........................... [45%]  20 passed
apps/api/tests/test_alerts.py   ........................ [55%]  24 passed
============================== 44 passed in 69.82s (0:01:09) =====================
```

### Full test suite (excludes test_copilot.py — that one needs a live API process)

```text
apps/api/tests/test_sensitivity.py ........................  21 passed
apps/api/tests/test_registry.py    .........................  5 passed
apps/api/tests/test_api.py         .........................  4 passed
apps/api/tests/test_forecast.py    ........................  20 passed
apps/api/tests/test_alerts.py      ........................  24 passed
============================== 74 passed in 106.50s (0:01:46) ===================
```

### TypeScript typecheck

```text
$ cd apps/web && npx tsc --noEmit
EXIT=0
```

---

## 5 · curl smoke tests (against live API on :8769)

### Forecast

```text
$ curl GET /api/forecast/profiles
{ "count": 3, "profiles": [
    { "line_id": "residential",     "series_count": 4, "attribution_count": 4 },
    { "line_id": "retail",          "series_count": 4, "attribution_count": 4 },
    { "line_id": "retail-leasing",  "series_count": 4, "attribution_count": 4 }
]}

$ curl GET /api/forecast/profiles/residential
residential: 4 series
  dynamic_irr:        linear_trend h=12
  payment_completion: ema h=12
  dedup_rate:         sma h=12
  channel_fee_ratio:  seasonal_naive h=12

$ curl POST /api/forecast/run  {line_id: "residential", indicator_id: "dynamic_irr", horizon: 12, method: "linear_trend", include_attribution: true}
line_id = residential
indicator = 动态 IRR (linear_trend)
historical = 24, forecast = 12
MAPE = 0.01048, bias = -0.00425, confidence = 0.95
attribution rows = 4
first 3 forecast: [
  {"period":"2026-10","point":0.566,"lower":0.544,"upper":0.589,"is_actual":false},
  {"period":"2026-11","point":0.567,"lower":0.535,"upper":0.599,"is_actual":false},
  {"period":"2026-12","point":0.567,"lower":0.528,"upper":0.607,"is_actual":false}
]
```

### Alerts

```text
$ curl GET /api/alerts/rules/residential
rule_count = 5
  irr_below_threshold: op=<       sev=high   scope=project
  payment_drop:        op=change_pct sev=medium scope=project
  redline_breach:      op===      sev=high   scope=project
  dedup_stall:         op=<       sev=medium scope=project  (consecutive=2)
  irr_between_band:    op=between sev=low    scope=project

$ curl POST /api/alerts/check  {line_id: "residential"}
rules_evaluated = 5
alerts_triggered = 2
summary = {"critical":0,"high":1,"medium":1,"low":0}
  -> rule=irr_below_threshold sev=high
       msg="residential 动态 IRR 6.95%，低于阈值 10%"
  -> rule=dedup_stall         sev=medium
       msg="residential 连续 2 月去化率 < 50%，需提质案场转化"
first alert id: 07317310-b015-4773-894b-84bb9230791a

$ curl GET /api/alerts/history?limit=10
total = 2, items = 2

$ curl POST /api/alerts/acknowledge/{id}
ack id = 07317310-b015-4773-894b-84bb9230791a, acknowledged = True

$ curl DELETE /api/alerts/{id}
delete result = {"deleted":"07317310-..."}
second delete → 404 "alert not found: 07317310-..."
```

### Pages (Next.js dev on :3000)

```text
GET /forecast    200
GET /alerts      200
GET /residential 200
GET /sensitivity 200
GET /copilot     200
GET /dashboard   200
```

### Topbar / line-overview shortcuts (HTML grep)

```text
/forecast HTML contains "滚动预测"  → True
/forecast HTML contains "告警中心"  → True
/residential HTML contains "滚动预测" → True
/residential HTML contains "告警中心" → True
```

---

## 6 · Universality test (add a 5th line, no engine code change)

Procedure: dropped `business_lines/test-line/{forecast.yaml, alerts.yaml, manifest.yaml}` and added one line to `registry.yaml`, then hit the API. The new line was auto-discovered, and both engines produced results.

```text
$ curl GET /api/forecast/profiles       (after add)
count = 4
  residential: 4 series
  retail: 4 series
  retail-leasing: 4 series
  test-line: 1 series             ← auto-discovered

$ curl GET /api/forecast/profiles/test-line
  test_kpi: method=sma horizon=6

$ curl POST /api/forecast/run {line_id: "test-line", indicator_id: "test_kpi"}
method = sma, historical = 12, forecast = 6, MAPE = 0.007486

$ curl GET /api/alerts/rules/test-line
  always_fire: 永远触发  op=<

$ curl POST /api/alerts/check {line_id: "test-line"}
rules_evaluated = 1, alerts_triggered = 1
  -> test-line 数值 1.0
```

After verification, the test-line directory was removed and `registry.yaml` reverted. Confirmed: zero engine code changes are required to add a 5th line.

---

## 7 · Assumptions

1. **Historical data is mocked** by `_generate_history(indicator_id, n)` in `forecast_engine.py` and `_mock_periods(target_id, indicator_id, n)` in `alert_engine.py`. They are deterministic per (indicator, target) so the same call returns the same series — useful for repeatability. When real historical data is wired in (e.g. from the dbt marts or ClickHouse), only the mock functions need to be replaced; the engine math stays the same.
2. **Target lists** for alerts (projects / properties) are resolved by calling each line's `/projects` or `/properties` endpoint with a 0.5-second timeout. If the line API is unreachable, the engine falls back to a single line-level target so rules still fire. This makes the system work in dev (when only the cross-line API is up) and in prod.
3. **Triggered alerts are in-memory.** A process restart wipes the store. That's fine for the demo. A future iteration could move the store to Redis or Postgres.
4. **MAPE / bias are computed on the last 6 historical periods** as a model-quality sanity check; they're informational, not blocking.
5. **95% CI half-width** for the linear_trend forecast grows as `z * sigma * sqrt(h)` where `h` is the horizon step. Bands visibly widen for longer horizons.
6. **Attribution is mocked.** Real deviation attribution would compare predicted vs actual per factor — out of scope for this iteration.
7. **Severity weights** (4 buckets × {0.30, 0.30, 0.20, 0.20}) are the same mock split used by the existing sensitivity engine. They sum to 1.0 and the heaviest buckets (market / project) reflect typical BP priorities.
8. **Frontend polling** is the in-app channel (every 10s). Email / webhook channels are reserved in the rule schema but not implemented.
9. **No new dependencies** were added to either `apps/api/pyproject.toml` or `apps/web/package.json`.

---

## 8 · Blockers / open issues

None.

- `pytest apps/api/tests --ignore=apps/api/tests/test_copilot.py -q` → 74 passed
- `npm run typecheck` (npx tsc --noEmit) → exit 0
- Next.js dev compile + page render: all 6 verified pages return 200
- Universality test: confirmed
- Cleanup: temporary test-line directory removed via PowerShell Recycle Bin

---

## 9 · Quick navigation

- Engine source: `apps/api/app/services/forecast_engine.py`, `apps/api/app/services/alert_engine.py`
- HTTP routes: `apps/api/app/routers/forecast.py`, `apps/api/app/routers/alerts.py`
- Frontend pages: `apps/web/app/(dashboard)/forecast/page.tsx`, `apps/web/app/(dashboard)/alerts/page.tsx`
- Per-line config: `business_lines/{residential,retail,retail-leasing}/{forecast.yaml,alerts.yaml}`
- Tests: `apps/api/tests/test_forecast.py`, `apps/api/tests/test_alerts.py`
