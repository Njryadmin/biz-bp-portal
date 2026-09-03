# Changelog

All notable changes to the fin-bp-portal project are recorded here.
The latest changes appear at the top.

## 2026-09-03 — Copilot 答案文本动态化（修最后 1 个硬编码）

- `apps/api/app/services/llm/mock_helpers.py`: 8 处硬编码 "住宅线" / "住宅下"
  全部改成 `f"{_line_label(line)}线"` / `f"{_line_label(line)}下"`，让答案文本
  跟随实际命中的业务线。`_LINE_DISPLAY_NAMES` 字典覆盖全部 10 条业务线的中文别名。
  原 4 条业务线（住宅/零售/零售租赁/测试）行为无回归。
- 测试：问"投资部 IRR 最高" → `未能从投资线 /projects 端点获取项目数据` ✓
- 测试：问"住宅三道红线" → `住宅线下,有 2 个项目触发了至少一道三道红线阈值` ✓

## 2026-09-03 — Copilot 顶层 `line_id` 字段 + 引擎修复

- `apps/api/app/services/copilot_engine.py`: `CopilotResponse` 新增 `line_id`
  字段。`parse_question` 已经能从问句里识别 line，但响应里只在
  `debug.parsed.line` 暴露，前端读不到。`_build_mock_response` 和
  `_ask_real_llm_async` 都填上 `line_id = parsed.get("line") or req.line_id`。
- `apps/api/app/services/llm/mock.py`: `_LINE_KEYWORDS` 改用
  `build_line_keywords_from_registry()` 动态生成（之前硬编码原 4 条）。
  加 tie-breaker（最长匹配 + 命中数 + line id 字典序）解决 "项目" 覆盖 "投资" bug。
- 测试：问"估价部 IRR 最高" → `line_id=valuation` ✓
- 测试：问"投资部 IRR 最高" → `line_id=investment` ✓（tie-breaker 修复证据）

## 2026-09-03 — API 启动优化（init_db 不再挂死）

- `apps/api/app/db/session.py`: `create_async_engine` 加
  `connect_args={"server_settings": {"connect_timeout": "2"}}` 防止 asyncpg
  默认无超时。
- `apps/api/app/db/bootstrap.py`: 新增 `DB_BOOTSTRAP_TIMEOUT_S = 2.0` 常量，
  `ensure_raw_schema` 套 `asyncio.wait_for(..., timeout=2.0)`。
- `apps/api/app/main.py`: lifespan 里 `init_db()` 套 try/except，DB 不可达时
  `log.warning` 继续启动 API，不杀掉整个进程。
- 测试：API 启动 3.35s（之前无限挂死）。warning 出现但 uvicorn 继续 ready。

## 2026-09-02 — Frontend SSR fix

Frontend rendering bugs surfaced during the dashboard smoke test, fixed
on the spot to unblock the dynamic-routing rollout. No behaviour change
for end users; documented here so the next iteration has a trail.

- `apps/web/app/(dashboard)/dashboard/page.tsx`: added `'use client'`.
  `@ant-design/icons` uses `React.createContext` internally, which is
  not available in server components. The page now runs as a client
  component and fetches registry data via the BFF proxy.
- `apps/web/app/(dashboard)/layout.tsx`: rewritten as a client
  component using plain HTML + flexbox. The previous implementation
  used `antd`'s `Layout` component in a server context, which hit
  Next.js 14's "Could not find the module in the React Client
  Manifest" error due to antd's barrel-optimized imports under RSC.
  Plain HTML sidesteps the issue and stays SSR-safe.
- `apps/web/app/api/registry/route.ts`: default API port
  `8000` → `8769`. The dev API was already running on 8769; the proxy
  default now matches so `apps/web` works without an env override.
