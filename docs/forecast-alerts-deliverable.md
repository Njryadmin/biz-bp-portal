# 预测与告警 — 交付物

> **本次交付模块：** 滚动预测引擎（模块 1）+ 告警中心（模块 2）
> **日期：** 2026-09-02
> **代码量：** 2 个引擎，2 个路由，2 棵 BFF 代理树，2 个页面，6 份 YAML 配置，44 个新测试，全部通过。
> **状态：** PASS

---

## 1 · 文件清单

### 后端（Python · FastAPI）

| 路径 | 用途 |
|---|---|
| `apps/api/app/services/forecast_engine.py` | 通用滚动预测引擎。读取 `business_lines/<line>/forecast.yaml`；支持 `sma` / `ema` / `linear_trend` / `seasonal_naive`；返回历史 + 12 个月预测，含 95% CI、MAPE、bias 以及可选的归因。 |
| `apps/api/app/services/alert_engine.py` | 通用告警引擎。读取 `business_lines/<line>/alerts.yaml`；支持 `>` `<` `>=` `<=` `==` `between` `change_pct` 运算符以及 `consecutive: N`；内存存储；模板化消息渲染。 |
| `apps/api/app/routers/forecast.py` | 跨业务线 HTTP 路由，挂载在 `/api/forecast/*`。 |
| `apps/api/app/routers/alerts.py` | 跨业务线 HTTP 路由，挂载在 `/api/alerts/*`。 |
| `apps/api/app/main.py` | `app.include_router(forecast_router)` + `app.include_router(alerts_router)`（紧邻 sensitivity/copilot）。注册表无变更。 |
| `apps/api/tests/test_forecast.py` | 20 个测试 —— profile 加载、4 种方法、MAPE/bias、归因、HTTP、通用性。 |
| `apps/api/tests/test_alerts.py` | 24 个测试 —— profile 加载、6 种运算符、`consecutive`、summary、ack/delete、history 分页、HTTP、通用性。 |

### 后端配置（YAML，按业务线）

| 路径 | 系列 / 规则 |
|---|---|
| `business_lines/residential/forecast.yaml` | 4 个系列（dynamic_irr, payment_completion, dedup_rate, channel_fee_ratio）+ 4 个归因分桶 |
| `business_lines/retail/forecast.yaml` | 4 个系列（noi, efficiency, collection_rate, vacancy_rate）+ 4 个归因分桶 |
| `business_lines/retail-leasing/forecast.yaml` | 4 个系列（occupancy_rate, avg_deal_rent, benchmark_gap_pct, renewal_rate）+ 4 个归因分桶 |
| `business_lines/residential/alerts.yaml` | **5 条规则**（irr_below_threshold, payment_drop, redline_breach, dedup_stall, irr_between_band）+ 4 个归因分桶 |
| `business_lines/retail/alerts.yaml` | **5 条规则**（noi_drop, collection_below, vacancy_spike, vacancy_consecutive_high, efficiency_below_band）+ 4 个归因分桶 |
| `business_lines/retail-leasing/alerts.yaml` | **5 条规则**（occupancy_below, vacancy_days_high, renewal_drop, benchmark_gap_negative, renewal_consecutive_low）+ 4 个归因分桶 |

### 前端（TypeScript · Next.js 14 / AntD 5 / ECharts 5）

| 路径 | 用途 |
|---|---|
| `apps/web/app/(dashboard)/forecast/page.tsx` | 预测页面 —— 左侧参数面板（业务线/指标/方法/horizon/include_attribution），右侧带 95% CI 区间的折线图，MAPE/bias/confidence 统计，归因表。 |
| `apps/web/app/(dashboard)/alerts/page.tsx` | 告警中心 —— 顶部条（业务线选择 + 严重度 tab + [立即检查]），触发告警卡片含严重度色条 + 确认/忽略动作，规则列表（可折叠）。10 秒轮询保证站内送达。 |
| `apps/web/app/(dashboard)/_components/Topbar.tsx` | 新增 2 个链接：滚动预测（`/forecast`）+ 告警中心（`/alerts`），带图标。 |
| `apps/web/app/(dashboard)/[line]/page.tsx` | 新增 2 个跨业务线快捷卡片：滚动预测 + 告警中心（均带 `?line=` 预选）。 |
| `apps/web/app/api/forecast/profiles/route.ts` | BFF 代理 —— 列出 profile |
| `apps/web/app/api/forecast/profiles/[line_id]/route.ts` | BFF 代理 —— 获取单个 profile |
| `apps/web/app/api/forecast/run/route.ts` | BFF 代理 —— POST /run |
| `apps/web/app/api/forecast/compare/route.ts` | BFF 代理 —— POST /compare（实际值 vs 预测值差异） |
| `apps/web/app/api/alerts/profiles/route.ts` | BFF 代理 —— 列出告警 profile |
| `apps/web/app/api/alerts/rules/[line_id]/route.ts` | BFF 代理 —— 列出规则 |
| `apps/web/app/api/alerts/rules/[line_id]/summary/route.ts` | BFF 代理 —— 规则 summary |
| `apps/web/app/api/alerts/check/route.ts` | BFF 代理 —— POST /check |
| `apps/web/app/api/alerts/history/route.ts` | BFF 代理 —— GET /history（带 line_id, limit, offset） |
| `apps/web/app/api/alerts/acknowledge/[alert_id]/route.ts` | BFF 代理 —— POST /acknowledge |
| `apps/web/app/api/alerts/[alert_id]/route.ts` | BFF 代理 —— DELETE（软删除） |

未新增 npm 包，`package.json` 未变。

---

## 2 · 3 条业务线 forecast profile 摘要

| 业务线 | 系列 | 使用方法 |
|---|---|---|
| residential | `dynamic_irr`, `payment_completion`, `dedup_rate`, `channel_fee_ratio` (4) | linear_trend, ema, sma, seasonal_naive |
| retail | `noi`, `efficiency`, `collection_rate`, `vacancy_rate` (4) | linear_trend, ema, sma, seasonal_naive |
| retail-leasing | `occupancy_rate`, `avg_deal_rent`, `benchmark_gap_pct`, `renewal_rate` (4) | ema, linear_trend, sma, seasonal_naive |

## 3 · 3 条业务线告警规则摘要

| 业务线 | 规则（各 5 条） | 覆盖运算符 |
|---|---|---|
| residential | irr_below_threshold（`<`+consecutive）、payment_drop（change_pct）、redline_breach（`==`）、dedup_stall（`<`+consecutive 2）、irr_between_band（between） | `<` `change_pct` `==` `between` consecutive |
| retail | noi_drop（change_pct）、collection_below（`<`）、vacancy_spike（`>`）、vacancy_consecutive_high（`>`+consecutive 3）、efficiency_below_band（between） | `<` `>` `change_pct` `between` consecutive |
| retail-leasing | occupancy_below（`<`）、vacancy_days_high（`>`）、renewal_drop（change_pct）、benchmark_gap_negative（`<`）、renewal_consecutive_low（`<`+consecutive 2） | `<` `>` `change_pct` consecutive |

---

## 4 · 测试结果

### 新增测试

```text
apps/api/tests/test_forecast.py ........................... [45%]  20 passed
apps/api/tests/test_alerts.py   ........................ [55%]  24 passed
============================== 44 passed in 69.82s (0:01:09) =====================
```

### 完整测试套件（排除 test_copilot.py —— 该测试需要运行中的 API 进程）

```text
apps/api/tests/test_sensitivity.py ........................  21 passed
apps/api/tests/test_registry.py    .........................  5 passed
apps/api/tests/test_api.py         .........................  4 passed
apps/api/tests/test_forecast.py    ........................  20 passed
apps/api/tests/test_alerts.py      ........................  24 passed
============================== 74 passed in 106.50s (0:01:46) ===================
```

### TypeScript 类型检查

```text
$ cd apps/web && npx tsc --noEmit
EXIT=0
```

---

## 5 · curl 冒烟测试（针对 :8769 上的运行 API）

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

### 页面（Next.js dev on :3000）

```text
GET /forecast    200
GET /alerts      200
GET /residential 200
GET /sensitivity 200
GET /copilot     200
GET /dashboard   200
```

### Topbar / 业务线概览快捷（HTML grep）

```text
/forecast HTML contains "滚动预测"  → True
/forecast HTML contains "告警中心"  → True
/residential HTML contains "滚动预测" → True
/residential HTML contains "告警中心" → True
```

---

## 6 · 通用性测试（新增第 5 条业务线，引擎代码 0 改动）

步骤：在 `business_lines/test-line/` 中放入 `{forecast.yaml, alerts.yaml, manifest.yaml}` 并向 `registry.yaml` 添加一行，然后调用 API。新业务线被自动发现，两个引擎都产生了结果。

```text
$ curl GET /api/forecast/profiles       (after add)
count = 4
  residential: 4 series
  retail: 4 series
  retail-leasing: 4 series
  test-line: 1 series             ← 自动发现

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

验证完成后，test-line 目录已移除，`registry.yaml` 已回滚。结论：新增第 5 条业务线无需任何引擎代码改动。

---

## 7 · 假设

1. **历史数据** 由 `forecast_engine.py` 中的 `_generate_history(indicator_id, n)` 和 `alert_engine.py` 中的 `_mock_periods(target_id, indicator_id, n)` 模拟生成。对相同的 (indicator, target) 是确定性的，方便重复实验。当接入真实历史数据（例如来自 dbt marts 或 ClickHouse）时，只需替换 mock 函数；引擎数学逻辑保持不变。
2. **告警的目标列表**（项目 / 物业）通过以 0.5 秒超时调用各业务线的 `/projects` 或 `/properties` 端点解析。若业务线 API 不可达，引擎回退到单一业务线级目标，使规则仍能触发。这让系统既能跑在 dev（仅跨业务线 API 在线）也能跑在 prod。
3. **触发的告警保存在内存中。** 进程重启会清空存储，demo 阶段无影响。后续可以将存储迁移到 Redis 或 Postgres。
4. **MAPE / bias** 基于最近 6 个历史周期计算，作为模型质量的健康检查，仅供参考，不阻塞流程。
5. **95% CI 半宽** 对 linear_trend 预测按 `z * sigma * sqrt(h)` 增长，其中 `h` 是预测期数。区间随预测期数变长而明显变宽。
6. **归因是 mock。** 真实偏差归因应逐因子比较预测值与实际值 —— 不在本迭代范围。
7. **严重度权重**（4 个分桶 × {0.30, 0.30, 0.20, 0.20}）与现有 sensitivity 引擎使用的同一套 mock 划分。总和为 1.0，权重最大的分桶（市场 / 项目）反映典型 BP 的优先级。
8. **前端轮询** 为站内通道（每 10 秒）。Email / webhook 通道在规则 schema 中已预留，未实现。
9. **未新增任何依赖** 到 `apps/api/pyproject.toml` 或 `apps/web/package.json`。

---

## 8 · 阻塞 / 未解决问题

无。

- `pytest apps/api/tests --ignore=apps/api/tests/test_copilot.py -q` → 74 passed
- `npm run typecheck`（npx tsc --noEmit）→ exit 0
- Next.js dev 编译 + 页面渲染：6 个已验证页面均返回 200
- 通用性测试：通过
- 清理：临时 test-line 目录已通过 PowerShell 回收站移除

---

## 9 · 快速导航

- 引擎源码：`apps/api/app/services/forecast_engine.py`、`apps/api/app/services/alert_engine.py`
- HTTP 路由：`apps/api/app/routers/forecast.py`、`apps/api/app/routers/alerts.py`
- 前端页面：`apps/web/app/(dashboard)/forecast/page.tsx`、`apps/web/app/(dashboard)/alerts/page.tsx`
- 按业务线配置：`business_lines/{residential,retail,retail-leasing}/{forecast.yaml,alerts.yaml}`
- 测试：`apps/api/tests/test_forecast.py`、`apps/api/tests/test_alerts.py`
