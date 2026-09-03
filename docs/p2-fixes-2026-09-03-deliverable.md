# P2 架构审查修复 — 2026-09-03

## 概要

3 项 P2 结论的根因相同：硬编码的业务线元数据会与 `business_lines/registry.yaml`
和每个 `<line>/manifest.yaml` 不同步。修复后，相关运行时结构**从在线
注册表动态构建**，因此 10 条已注册业务线（以及任何未来的业务线）都
自动获得正确覆盖。

| # | 文件 | 症状 | 修复 |
|---|------|------|------|
| P2 #1 | `apps/api/app/services/llm/prompts.py` | `ENDPOINT_CATALOG` 硬编码 4 条业务线，LLM 对另外 6 条一无所知 | 新增 `build_endpoint_catalog()` 从注册表读取 `manifest.nav[]` + `api_prefix` |
| P2 #2 | `apps/api/app/services/copilot_engine.py` | `LINE_SUGGESTIONS` 硬编码 3 条业务线，6 条新业务线掉到 "common" | 新增 `build_line_suggestions()` 为每条已注册业务线生成 4 个模板化问题 |
| P2 #3 | `apps/web/app/(dashboard)/_components/linePageConfig.ts` | `LINE_PAGE_SPECS` 硬编码 4 条业务线，6 条新业务线渲染为 "not-integrated" | 新增 `buildLinePageConfig()` + 运行时缓存；`(line, page)` 对从 `nav[]` 自动派生 |

**结果：3 项 P2 全部 PASS。** 新增测试文件 `apps/api/tests/test_p2_universality.py`
带来 15 个回归测试（通用性 + catalog/suggestion/启发式正确性）。

---

## 变更的文件

```
apps/api/app/services/llm/prompts.py                           （修改：动态 ENDPOINT_CATALOG）
apps/api/app/services/copilot_engine.py                        （修改：动态 LINE_SUGGESTIONS）
apps/web/app/(dashboard)/_components/linePageConfig.ts        （重写：动态 builder + 运行时缓存）
apps/web/app/(dashboard)/[line]/[page]/page.tsx                （修改：注册表加载时调用 setLinePageConfig）
apps/web/app/(dashboard)/[line]/page.tsx                       （修改：注册表加载时调用 setLinePageConfig）
apps/api/tests/test_p2_universality.py                         （新增：15 个回归测试）
```

无新增依赖。保留向后兼容的别名（`ENDPOINT_CATALOG`、`LINE_SUGGESTIONS`），
作为只读的模块级绑定，使旧代码仍可工作。

---

## P2 #1 — `build_endpoint_catalog()`

**功能。** 读取 `business_lines/registry.yaml` 中的每个条目，
解析其 `manifest.yaml`（Pydantic 的 `BusinessLine` 模型已存在），
每个 nav 条目生成一行。URL 格式：`{api_prefix}/{nav_slug}` —
例如 `manifest.nav = "/valuation/reports"` + `api_prefix = "/api/lines/valuation"`
→ `GET /api/lines/valuation/reports — 报告明细`。

**缓存策略。** 模块导入时构建一次，既暴露为函数（`build_endpoint_catalog()`）
用于重建，也暴露为模块级代理（`endpoint_catalog()`）用于热读。
旧版 `ENDPOINT_CATALOG` dict 现在是该缓存的只读别名。

**验证（curl，端口 8770）：**

```text
$ python -c "from app.services.llm.prompts import render_system_prompt, build_endpoint_catalog; \
              sp = render_system_prompt(); \
              c = build_endpoint_catalog(); \
              print('valuation in catalog:', 'valuation' in c); \
              print('api_prefix present:', '/api/lines/valuation' in ' | '.join(c['valuation'])); \
              print('reports slug mapped correctly:', '/api/lines/valuation/reports' in ' | '.join(c['valuation'])); \
              print('no duplicated line id:', '/api/lines/valuation/valuation' not in ' | '.join(c['valuation'])); \
              print('system_prompt mentions valuation:', 'valuation' in sp); \
              print('system_prompt contains /api/lines/valuation/reports:', '/api/lines/valuation/reports' in sp)"
valuation in catalog: True
api_prefix present: True
reports slug mapped correctly: True
no duplicated line id: True
system_prompt mentions valuation: True
system_prompt contains /api/lines/valuation/reports: True
```

（目录条目中的中文字符在 PowerShell 控制台输出中显示为乱码，但底层字符串
是正确的 UTF-8。）

---

## P2 #2 — `build_line_suggestions()`

**功能。** 对每条已注册业务线，生成 4 个中文模板化问题：

1. `{display_name} 的核心 KPI({first_indicator.title})概览`
2. `对 {display_name} 做一份敏感性分析`
3. `对 {display_name} 做未来 12 期预测`
4. `检查 {display_name} 是否有告警`

首个 indicator 标题来自 `indicators.yaml`（例如 residential 是
"动态 IRR"，valuation 是 "估价报告数"，investment 是 "AUM (资产管理规模)"）。
没有 `indicators.yaml` 的业务线回退到通用占位符"核心指标"。

**缓存策略。** 与 P2 #1 相同：模块导入时构建一次（`_LINE_SUGGESTIONS`），
既暴露为函数，也暴露为只读别名（`LINE_SUGGESTIONS`）。`CopilotEngine.suggestions()`
按当前已注册业务线过滤缓存（防御性，防止注册表在模块初始化和请求处理之间
被卸载）。

**验证（curl，端口 8770）：**

```text
$ curl http://127.0.0.1:8770/api/copilot/suggestions | jq '.by_line | keys'
[
  "advisory",
  "industrial",
  "investment",
  "my-line",
  "office-leasing",
  "project-management",
  "residential",
  "retail",
  "retail-leasing",
  "valuation"
]
```

10 条业务线，每条 4 个建议。`valuation` 的样例建议：

```text
- 估价部 的核心 KPI(估价报告数)概览
- 对 估价部 做一份敏感性分析
- 对 估价部 做未来 12 期预测
- 检查 估价部 是否有告警
```

**通用性检查。** 添加临时 `test-line`（含 `manifest.yaml` +
`indicators.yaml`，含一个 "Test Headline" indicator），重启 API 后
再次 curl：

```text
$ curl http://127.0.0.1:8770/api/copilot/suggestions | jq '.by_line["test-line"]'
[
  "测试业务线 的核心 KPI(Test Headline)概览",
  "对 测试业务线 做一份敏感性分析",
  "对 测试业务线 做未来 12 期预测",
  "检查 测试业务线 是否有告警"
]
```

头条 KPI 标题从 `indicators.yaml` 插值而来，无需任何代码改动 ——
正是架构审查所要求的通用性。

---

## P2 #3 — `buildLinePageConfig()`（TypeScript）

**功能。** 用纯函数 `buildLinePageConfig(lines: BusinessLine[])`
取代硬编码的 `LINE_PAGE_SPECS` 表，遍历 manifest 的 `nav[]`，产出
`{lineId: {slug: PageSpec}}` 映射。启发式：

- 显式按业务线覆盖（如 `retail-leasing:market-report` → `market-benchmark`）
- 对 slug + nav 标题的正则规则（如 `/report|accuracy|.../i → project-detail`、
  `/noi|brand|renovat|.../i → property-detail`）
- 按业务线分组的回退（暴露 `/properties` 的业务线默认 `property-detail`；
  其它回退到 `project-detail`）

**缓存策略。** 运行时缓存 `LIVE_LINE_PAGE_SPECS` 在每次页面加载时由
`[line]/page.tsx` 和 `[line]/[page]/page.tsx` 中 `/api/registry` 的
fetch 填充。同步的 `getPageSpec(line, page)` API 仍然工作（注册表
解析前返回温和的 "line-overview"，之后返回完整映射）。旧版
`LINE_PAGE_SPECS` 常量保留为空默认值，方便任何直接 import 它的代码。

**验证（TypeScript）：**

```text
$ cd apps/web && npx tsc --noEmit
（无输出 — 干净）
```

**启发式 pinning（Python 镜像）。** 4 条 `SLUG_KIND_RULES` + 4 条
`KNOWN_KIND_OVERRIDES` 镜像在
`apps/api/tests/test_p2_universality.py::TestLinePageConfigHeuristic`，
任何规则变更都会在后端触发测试失败。样例断言：

- `valuation / valuation/reports / 报告明细` → `project-detail`
- `retail-leasing / retail-leasing/market-report / 市场对标` → `market-benchmark`（覆盖）
- `office-leasing / office-leasing/area / 成交面积` → `property-detail`（回退）
- `my-line / my-line / 概览` → `ping-only`（概览覆盖）
- `my-line / my-line/ping / ping` → `not-integrated`（覆盖）
- `investment / investment/portfolio / 投资组合` → `project-detail`（规则）

---

## 测试输出

```text
$ cd apps/api && python -m pytest tests/test_p2_universality.py tests/test_llm_backends.py \
                                  tests/test_copilot.py tests/test_registry.py
81 passed, 1 warning in 7.72s
```

- `test_p2_universality.py` 中 15 个新的 P2 测试
- `test_llm_backends.py` 中 36 个已有 LLM 测试（未变）
- `test_copilot.py` 中 25 个已有 copilot 测试（未变）
- `test_registry.py` 中 5 个已有注册表测试（未变）

**新测试数：15**（4 个 catalog + 4 个 suggestions + 7 个启发式）。
通用性测试（add-a-line、restart、verify）占 catalog/suggestion 测试
的 2 个。

---

## Curl 验证（端口 8770 — 重启后的全新 API）

| 端点 | 状态 | 结果 |
|----------|--------|--------|
| `GET /api/copilot/health` | 200 | `available_lines` = 全部 10 条 |
| `GET /api/copilot/suggestions` | 200 | `by_line` 含 10 条业务线，每条 4 个建议 |
| `GET /api/registry/lines` | 200 | 10 条业务线，每条带完整 `nav[]` 数组 |
| `GET /api/copilot/ask`（line_id=valuation，q=valuation 的指标） | 200 | `line_id: "valuation"`，debug 显示正确解析 |
| `GET /api/copilot/ask`（line_id=valuation，q=valuation 的核心指标） | 200 | `line_id: "valuation"`，mock 回退到建议列表（符合预期 —— mock 意图模板有限） |
| `GET /api/copilot/ask`（line_id=test-line，add 之后） | 200 | `line_id: "test-line"`，mock 渲染模板化建议 |
| `GET /api/registry` 代理 | 200 | 返回与 `/api/registry/lines` 相同的 payload |

system prompt 的 `business_lines` 部分现在包含全部 10 条业务线
（通过渲染 `render_system_prompt()` 并检查每个 line id + 其
`api_prefix` + 至少一个 nav-slug 派生的端点来验证）。

---

## 已知后续工作（不在本次范围）

1. **新业务线的 mock intent 模板。** `apps/api/app/services/llm/mock.py`
   中有意图模板（irr_top、noi_top、vacancy 等）硬编码到最初的 4 条
   业务线。新业务线（`valuation`、`advisory`、…）即使 API 有数据，
   多数 intent 模式仍会落到 `fallback_unknown`。P2 #2 只修复了
   *建议*界面，没修*答案*界面。后续 P3 可以让 mock 在遇到未知业务线
   时派发到通用的 `line_indicators` 视图。

2. **注册表变化时的实时 API 重新挂载。** `apps/api/app/routers/registry.py`
   中的 `mount_business_line_routers` 路径只在 `lifespan` 启动时运行。
   新增业务线需要重启 API。mock 后端的 line-keyword builder 是唯一
   不需重启即可感知变化的部分，因为它在首次 import 时（而不是每个
   请求）重新读取 YAML。这是已存在的限制，并非 P2 #1/#2/#3 引入。

3. **`/api/registry/lines` schema 漂移。** web 应用的
   `apps/web/app/api/registry/route.ts` 代理到
   `${base}/api/registry/lines`（Python 端存在该端点）。schema 是
   来自 `@fin-bp/types` 的 `BusinessLine`。已确认与
   `buildLinePageConfig()` 输入形态兼容。

---

## 假设

- `load_registry()` 是纯函数（无 I/O），模块导入时调用是安全的。
  已通过阅读 `apps/api/app/core/registry.py` 确认。
- `BusinessLine` Pydantic 模型已暴露
  `nav: list[BusinessLineNavItem]`（已在注册表测试中验证）。无需 schema 变更。
- `indicators.yaml` 首个 indicator 标题是业务线"头条 KPI"的稳定代理
  （residential 的"动态 IRR"、valuation 的"估价报告数"）。已通过抽查
  10 个 manifest 确认。
- web 应用的 `/api/registry` 代理返回与 `/api/registry/lines` 相同的
  形态（通过 curl 验证）。
- PowerShell 控制台无法正确显示 CJK 字符串；底层 API 响应是合法 UTF-8，
  数据正确（通过 Python 端对同一响应的断言验证）。

## 阻塞

无。3 项 P2 全部修复，所有测试通过，curl 验证绿。
