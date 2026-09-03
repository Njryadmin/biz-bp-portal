# 敏感性 Lab — 交付物

**状态：** ✅ PASS
**模块：** 敏感性分析实验室（Sensitivity Lab）— 跨所有业务线通用
**日期：** 2026-09-02

## 结果

**PASS** —— 后端全部 30 个测试通过（9 个基线 + 21 个新增）。后端端点
响应正确。前端 `/sensitivity` 路由返回 HTTP 200。业务线快捷方式与顶部条
链接已渲染。通过临时第 5 条业务线端到端验证通用性。

## 后端变更

| 文件 | 类型 | 用途 |
| --- | --- | --- |
| `business_lines/residential/sensitivity.yaml` | 新增 | 住宅的 profile —— 4 输入 × 3 输出 |
| `business_lines/retail/sensitivity.yaml` | 新增 | 零售的 profile —— 4 输入 × 4 输出 |
| `business_lines/retail-leasing/sensitivity.yaml` | 新增 | 零售租赁的 profile —— 4 输入 × 4 输出 |
| `apps/api/app/services/sensitivity_engine.py` | 新增 | 通用引擎 —— Pydantic DTO、profile 加载器、基值解析、1D/2D 分析、tornado、scenarios、lru_cache |
| `apps/api/app/routers/sensitivity.py` | 新增 | FastAPI 路由（**不**走业务线自动发现；由 `app.main` 在根挂载） |
| `apps/api/app/main.py` | 编辑 | `app.include_router(sensitivity_router)` 位于 upload_router 之后 |
| `apps/api/tests/test_sensitivity.py` | 新增 | 21 个测试，覆盖 profile 加载、1D/2D 计算、tornado 排序、scenarios、错误路径、HTTP、通用性 |

**未修改** `business_lines/*/api/router.py`、`apps/api/app/routers/registry.py`、
或任何业务线种子数据。

## 前端变更

| 文件 | 类型 | 用途 |
| --- | --- | --- |
| `apps/web/app/(dashboard)/sensitivity/page.tsx` | 新增 | 客户端组件：参数面板（320px）+ 热力图 + tornado + scenarios 表格。首次加载 profile 时自动运行。 |
| `apps/web/app/(dashboard)/_components/Topbar.tsx` | 编辑 | 在 `RoleSwitcher` 之前的顶部条新增 `敏感性分析` 链接（`<ExperimentOutlined />`） |
| `apps/web/app/(dashboard)/[line]/page.tsx` | 编辑 | 在每条业务线导航网格末尾新增高亮的 `Sensitivity` 快捷卡片（链接到 `/sensitivity?line=<lineId>`） |
| `apps/web/app/api/sensitivity/profiles/route.ts` | 新增 | BFF 代理 → `GET /api/sensitivity/profiles` |
| `apps/web/app/api/sensitivity/profiles/[line_id]/route.ts` | 新增 | BFF 代理 → `GET /api/sensitivity/profiles/{line_id}` |
| `apps/web/app/api/sensitivity/analyze/route.ts` | 新增 | BFF 代理 → `POST /api/sensitivity/analyze` |
| `apps/web/app/api/sensitivity/scenarios/[line_id]/route.ts` | 新增 | BFF 代理 → `GET /api/sensitivity/scenarios/{line_id}` |

## 敏感性 profile

### residential（`business_lines/residential/sensitivity.yaml`）

**4 个输入：** `avg_price`（平均售价，±10%）、`dedup_speed`（去化速度，±20%）、
`construction_cost`（建安成本，±5%）、`channel_fee_rate`（渠道费率，±30%）。

**3 个输出：** `dynamic_irr`（动态 IRR，%）、`dynamic_net_margin`（动态净利率，%）、
`payment_completion`（回款完成率，%）。

系数节选（dynamic_irr）：
- `avg_price: +1.5`（售价+1%，IRR 上升 1.5pp）
- `dedup_speed: +0.3`
- `construction_cost: -0.8`
- `channel_fee_rate: -0.2`

### retail（`business_lines/retail/sensitivity.yaml`）

**4 个输入：** `avg_rent`（平均月租金，±10%）、`vacancy_rate`（空置率，±30%）、
`opex_ratio`（运营成本占比，±10%）、`collection_rate`（收缴率，±5%）。

**4 个输出：** `noi`（NOI 万元）、`efficiency`（坪效 元/㎡/月）、
`collection_rate`（收缴率 %）、`brand_diversity`（品牌多样性指数 0-1）。

系数节选（noi，NOI = EGR - OpEx）：
- `avg_rent: +480.0`
- `vacancy_rate: -8400.0`（空置率越低 NOI 越高）
- `opex_ratio: -3200.0`
- `collection_rate: +0.0`

### retail-leasing（`business_lines/retail-leasing/sensitivity.yaml`）

**4 个输入：** `avg_deal_rent`（平均成交租金，±10%）、`vacancy_rate`（空置率，±30%）、
`owner_vacancy_days`（业主空置期，±20%）、`renewal_rate`（续约率，±10%）。

**4 个输出：** `occupancy_rate`（商铺出租率 %）、`benchmark_gap_pct`（竞品基准对标差 %）、
`commission_revenue`（佣金收入 万元）、`renewal_rate`（续约率 %）。

系数节选（occupancy_rate）：
- `avg_deal_rent: +0.0`
- `vacancy_rate: -1.0`（空置率越低出租率越高，1:1）
- `owner_vacancy_days: -0.1`
- `renewal_rate: +0.3`

## 测试输出

```
$ python -m pytest -q
..............................                                           [100%]
============================== warnings summary ===============================
..\..\..\..\..\AppData\Local\Programs\Python\Python312\Lib\site-packages\fastapi\testclient.py:1
  ... StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
30 passed, 1 warning in ~36s
```

21 个新的敏感性测试：

```
$ python -m pytest tests/test_sensitivity.py -v
tests/test_sensitivity.py::test_load_profile_residential PASSED
tests/test_sensitivity.py::test_load_profile_retail PASSED
tests/test_sensitivity.py::test_load_profile_retail_leasing PASSED
tests/test_sensitivity.py::test_load_profile_unknown_line_raises PASSED
tests/test_sensitivity.py::test_list_profiles_returns_three PASSED
tests/test_sensitivity.py::test_analyze_1d_matrix_shape_and_base_point PASSED
tests/test_sensitivity.py::test_analyze_2d_matrix_shape_and_corners PASSED
tests/test_sensitivity.py::test_tornado_sorted_by_span PASSED
tests/test_sensitivity.py::test_scenarios_1d_three_items_base_in_middle PASSED
tests/test_sensitivity.py::test_scenarios_2d_seven_items_includes_corners PASSED
tests/test_sensitivity.py::test_unknown_output_id_raises_keyerror PASSED
tests/test_sensitivity.py::test_unknown_input_id_raises_keyerror PASSED
tests/test_sensitivity.py::test_http_profiles_endpoint PASSED
tests/test_sensitivity.py::test_http_profile_for_one_line PASSED
tests/test_sensitivity.py::test_http_profile_unknown_line_404 PASSED
tests/test_sensitivity.py::test_http_analyze_1d_success PASSED
tests/test_sensitivity.py::test_http_analyze_2d_success PASSED
tests/test_sensitivity.py::test_http_analyze_unknown_output_400 PASSED
tests/test_sensitivity.py::test_http_analyze_unknown_line_404 PASSED
tests/test_sensitivity.py::test_http_scenarios_endpoint PASSED
tests/test_sensitivity.py::test_universality_with_temp_line PASSED
21 passed, 1 warning in 35.93s
```

## 验证（9 项验收点）

| # | 标准 | 结果 |
| --- | --- | --- |
| 1 | `pytest tests/test_sensitivity.py -v` 全部通过 | ✅ 21 passed |
| 2 | `pytest -q` 仍 21+N 通过（无回归） | ✅ 30 passed（9 基线 + 21 新） |
| 3 | `GET /api/sensitivity/profiles` → 3 条业务线 | ✅ count=3（residential、retail、retail-leasing） |
| 4 | `GET /api/sensitivity/profiles/residential` → 4 输入 + 3 输出 | ✅ inputs=4, outputs=3 |
| 5 | `POST /api/sensitivity/analyze` 1D → 200 + 矩阵 | ✅ 矩阵 1×11，base 0.18，worst 0.03，best 0.33 |
| 6 | `POST /api/sensitivity/analyze` 2D → 200 + 矩阵（rows×cols） | ✅ 矩阵 11×11，top-left 0.07，top-right 0.37，bottom-left -0.01，bottom-right 0.29 |
| 7 | 错误：output_id 不在 profile → 400 | ✅ `400 -- {"detail":"bad request: output_id not in profile: not_a_real_output"}` |
| 8 | `npm run typecheck` → 通过 | ✅ 无错误 |
| 9 | `GET /sensitivity` → 200（且 `/residential` 仍 200） | ✅ 200 / 200；`/retail` 和 `/retail-leasing` 也 200 |

**通用性检查**（额外，9 项之外）：临时写入 `business_lines/test-line/sensitivity.yaml`（2 输入 1 输出）。重启 API 后，`GET /api/sensitivity/profiles/test-line` 返回 `inputs=2, outputs=1`，`POST /api/sensitivity/analyze` 正确应用系数（`alpha: +2.0, base=1.0` → -20% 扰动产生 0.6，+20% 产生 1.4）。临时行已删除，未触动引擎代码。

**前端截图：** 截图未嵌入（Windows 终端会话）；dev server 日志显示 Next.js 14.2.5 `✓ Ready in 1483ms`，路由 200、200、200、200 确认页面已加载。

## 假设

1. **线性系数。** 引擎使用 `output = base + Σ (coef × delta_input)`，即
   一阶 Taylor 近似。快速、确定、易审计，但无法捕捉输入之间的交互效应。
   已记录在引擎 docstring 中；系数按各 sensitivity.yaml 中合理的业务逻辑
   标定（例如 retail 的 `vacancy_rate → NOI` 强负相关）。
2. **默认路径不通过 HTTP 抓取业务的基值。** 引擎**可以**通过 HTTP 把
   `base_value_ref`（如 `kpi.dynamic_irr`）解析为业务线 `/indicators`
   端点的值，但仅对正在分析的 OUTPUT 这么做。输入的基值在结果中返回 0
   （仅供参考）。这让每次 `analyze()` 调用最多只需一次 HTTP 往返，避免
   对业务线 API 的高频请求。前端可以传 `base_overrides` 来固定基值。
3. **敏感性引擎是通用的。** 它绝不 `import business_lines/*`，只读取
   `business_lines/<line>/sensitivity.yaml`。新增第 5、10、100 条业务线
   都只需添加 YAML。
4. **引擎在进程内交付。** profile 加载使用 `lru_cache(maxsize=32)`；
   `clear_profile_cache()` 仅在测试中使用，平时不可见。生产环境中缓存
   与进程同生命周期 —— 这没问题，因为 YAML 改动需要重启 API（与加载器
   其它部分行为一致）。
5. **未新增依赖。** Pydantic v2（已在 pyproject）、FastAPI、PyYAML、
   urllib（stdlib）。刻意未引入 numpy，因为计算完全是一阶线性标量组合。
6. **HTTP 错误映射。** 错误 ID 表现为 400；缺失业务线表现为 404。
   遵循规格的契约："errors：output_id 不存在 → 400/404"（按 RFC 7231
   §6.5.1 对验证错误选用了 400）。
7. **前端默认运行。** 首次挂载时页面会用首条业务线的默认值自动跑一次，
   让用户立刻看到结果，而不是空卡片。

## 阻塞

**无。** 全部验收点通过；通用性通过临时第 5 条业务线（事后删除）端到端
验证。

## 文件概览

```
business_lines/
├── residential/sensitivity.yaml        # 4 in / 3 out
├── retail/sensitivity.yaml             # 4 in / 4 out
└── retail-leasing/sensitivity.yaml     # 4 in / 4 out

apps/api/
├── app/
│   ├── main.py                         # +1 行：include sensitivity_router
│   ├── routers/
│   │   └── sensitivity.py              # 新增：4 个端点
│   └── services/
│       └── sensitivity_engine.py       # 新增：~430 行，纯计算
└── tests/
    └── test_sensitivity.py             # 新增：21 个测试

apps/web/app/
├── (dashboard)/
│   ├── _components/Topbar.tsx          # + 敏感性分析 链接
│   ├── [line]/page.tsx                 # + Sensitivity 快捷卡片
│   └── sensitivity/page.tsx            # 新增：完整 lab UI
└── api/sensitivity/
    ├── profiles/route.ts               # 新增：BFF 代理（list）
    ├── profiles/[line_id]/route.ts     # 新增：BFF 代理（one）
    ├── analyze/route.ts                # 新增：BFF 代理
    └── scenarios/[line_id]/route.ts    # 新增：BFF 代理

docs/
└── sensitivity-deliverable.md          # 新增：本文件
```
