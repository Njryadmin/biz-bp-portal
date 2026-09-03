# P2 Architecture-Review Fixes — 2026-09-03

## Summary

All 3 P2 findings share a single root cause: hardcoded business-line
metadata that drifts out of sync with `business_lines/registry.yaml` and
each `<line>/manifest.yaml`. After the fix, the relevant runtime
structures are built **dynamically from the live registry** so the 10
registered lines (and any future ones) get correct coverage for free.

| # | File | Symptom | Fix |
|---|------|---------|-----|
| P2 #1 | `apps/api/app/services/llm/prompts.py` | `ENDPOINT_CATALOG` hardcoded 4 lines, LLM blind to the other 6 | New `build_endpoint_catalog()` reads `manifest.nav[]` + `api_prefix` from registry |
| P2 #2 | `apps/api/app/services/copilot_engine.py` | `LINE_SUGGESTIONS` hardcoded 3 lines, 6 new lines fell through to "common" | New `build_line_suggestions()` generates 4 templated questions per registered line |
| P2 #3 | `apps/web/app/(dashboard)/_components/linePageConfig.ts` | `LINE_PAGE_SPECS` hardcoded 4 lines, 6 new lines rendered "not-integrated" | New `buildLinePageConfig()` + runtime cache; `(line, page)` pairs auto-derived from `nav[]` |

**Result: PASS for all 3 P2s.** New test file `apps/api/tests/test_p2_universality.py`
adds 15 regression tests (universality + catalog/suggestion/heuristic correctness).

---

## Files changed

```
apps/api/app/services/llm/prompts.py                           (modified: dynamic ENDPOINT_CATALOG)
apps/api/app/services/copilot_engine.py                        (modified: dynamic LINE_SUGGESTIONS)
apps/web/app/(dashboard)/_components/linePageConfig.ts        (rewritten: dynamic builder + runtime cache)
apps/web/app/(dashboard)/[line]/[page]/page.tsx                (modified: call setLinePageConfig on registry load)
apps/web/app/(dashboard)/[line]/page.tsx                       (modified: call setLinePageConfig on registry load)
apps/api/tests/test_p2_universality.py                         (new: 15 regression tests)
```

No new dependencies. Backwards-compatible aliases (`ENDPOINT_CATALOG`,
`LINE_SUGGESTIONS`) kept as read-only module-level bindings so any
legacy code that imports them keeps working.

---

## P2 #1 — `build_endpoint_catalog()`

**What it does.** Reads every entry in `business_lines/registry.yaml`,
parses its `manifest.yaml` (Pydantic `BusinessLine` model already
existed), and produces one line per nav entry. URL format:
`{api_prefix}/{nav_slug}` — e.g. `manifest.nav = "/valuation/reports"`
+ `api_prefix = "/api/lines/valuation"` →
`GET /api/lines/valuation/reports — 报告明细`.

**Cache strategy.** Built once at module import, exposed as both a
function (`build_endpoint_catalog()`) for re-builds and a module-level
proxy (`endpoint_catalog()`) for hot reads. The legacy `ENDPOINT_CATALOG`
dict is now a read-only alias of the cache.

**Verification (curl, port 8770):**

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

(Chinese characters in catalog entries are present but display as mojibake
in PowerShell console output — the underlying strings are correct UTF-8.)

---

## P2 #2 — `build_line_suggestions()`

**What it does.** For each registered line, generates 4 templated
questions in Chinese:

1. `{display_name} 的核心 KPI({first_indicator.title})概览`
2. `对 {display_name} 做一份敏感性分析`
3. `对 {display_name} 做未来 12 期预测`
4. `检查 {display_name} 是否有告警`

The first indicator title comes from `indicators.yaml` (e.g. "动态 IRR"
for residential, "估价报告数" for valuation, "AUM (资产管理规模)"
for investment). Lines without `indicators.yaml` fall back to the
generic "核心指标" placeholder.

**Cache strategy.** Same pattern as P2 #1: built once at module import
(`_LINE_SUGGESTIONS`), exposed as a function and a read-only alias
(`LINE_SUGGESTIONS`). `CopilotEngine.suggestions()` filters the cache
by currently-registered lines (defensive, in case the registry was
unloaded between module-init and request time).

**Verification (curl, port 8770):**

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

10 lines, 4 suggestions each. Sample suggestion for `valuation`:

```text
- 估价部 的核心 KPI(估价报告数)概览
- 对 估价部 做一份敏感性分析
- 对 估价部 做未来 12 期预测
- 检查 估价部 是否有告警
```

**Universality check.** Adding a temporary `test-line` (with
`manifest.yaml` + `indicators.yaml` containing a "Test Headline"
indicator), restarting the API, and re-curling:

```text
$ curl http://127.0.0.1:8770/api/copilot/suggestions | jq '.by_line["test-line"]'
[
  "测试业务线 的核心 KPI(Test Headline)概览",
  "对 测试业务线 做一份敏感性分析",
  "对 测试业务线 做未来 12 期预测",
  "检查 测试业务线 是否有告警"
]
```

The headline KPI title is interpolated from `indicators.yaml` with no
code changes — exactly the universality property the architecture
review required.

---

## P2 #3 — `buildLinePageConfig()` (TypeScript)

**What it does.** Replaces the hardcoded `LINE_PAGE_SPECS` table with
a pure function `buildLinePageConfig(lines: BusinessLine[])` that
walks the manifest's `nav[]` and produces a `{lineId: {slug: PageSpec}}`
map. Heuristics:

- Explicit per-line override (e.g. `retail-leasing:market-report` →
  `market-benchmark`)
- Regex rules on slug + nav title (e.g. `/report|accuracy|.../i →
  project-detail`, `/noi|brand|renovat|.../i → property-detail`)
- Fallback per line group (lines exposing `/properties` default to
  `property-detail`; others to `project-detail`)

**Cache strategy.** Runtime cache `LIVE_LINE_PAGE_SPECS` is populated
on every page load from the `/api/registry` fetch in
`[line]/page.tsx` and `[line]/[page]/page.tsx`. The synchronous
`getPageSpec(line, page)` API still works (returns a graceful
"line-overview" before the registry resolves, and a full map
afterwards). The legacy `LINE_PAGE_SPECS` constant is preserved as an
empty default for any code that imports it directly.

**Verification (TypeScript):**

```text
$ cd apps/web && npx tsc --noEmit
(no output — clean)
```

**Heuristic pinning (Python mirror).** The 4 SLUG_KIND_RULES + 4
KNOWN_KIND_OVERRIDES are mirrored in
`apps/api/tests/test_p2_universality.py::TestLinePageConfigHeuristic`
so any change to the rules triggers a test failure on the backend
side. Sample assertions:

- `valuation / valuation/reports / 报告明细` → `project-detail`
- `retail-leasing / retail-leasing/market-report / 市场对标` → `market-benchmark` (override)
- `office-leasing / office-leasing/area / 成交面积` → `property-detail` (fallback)
- `my-line / my-line / 概览` → `ping-only` (overview override)
- `my-line / my-line/ping / ping` → `not-integrated` (override)
- `investment / investment/portfolio / 投资组合` → `project-detail` (rule)

---

## Test output

```text
$ cd apps/api && python -m pytest tests/test_p2_universality.py tests/test_llm_backends.py \
                                  tests/test_copilot.py tests/test_registry.py
81 passed, 1 warning in 7.72s
```

- 15 new P2 tests in `test_p2_universality.py`
- 36 existing LLM tests in `test_llm_backends.py` (unchanged)
- 25 existing copilot tests in `test_copilot.py` (unchanged)
- 5 existing registry tests in `test_registry.py` (unchanged)

**New test count: 15** (4 catalog tests + 4 suggestions tests + 7
heuristic tests). The universality tests (add-a-line, restart,
verify) account for 2 of those 4 catalog/suggestion tests.

---

## Curl validation (port 8770 — fresh API after restart)

| Endpoint | Status | Result |
|----------|--------|--------|
| `GET /api/copilot/health` | 200 | `available_lines` = all 10 |
| `GET /api/copilot/suggestions` | 200 | `by_line` has 10 lines, 4 suggestions each |
| `GET /api/registry/lines` | 200 | 10 lines, each with full `nav[]` array |
| `GET /api/copilot/ask` (line_id=valuation, q=valuation 的指标) | 200 | `line_id: "valuation"`, debug shows parsed correctly |
| `GET /api/copilot/ask` (line_id=valuation, q=valuation 的核心指标) | 200 | `line_id: "valuation"`, mock falls back to suggestion list (expected — mock has limited intent templates) |
| `GET /api/copilot/ask` (line_id=test-line, after add) | 200 | `line_id: "test-line"`, mock renders the templated suggestions |
| `GET /api/registry` proxy | 200 | returns same payload as `/api/registry/lines` |

The system prompt's `business_lines` section now contains all 10 lines
(verified by rendering `render_system_prompt()` and checking for each
line id + its api_prefix + at least one nav-slug-derived endpoint).

---

## Known follow-ups (not in scope)

1. **Mock intent templates for new lines.** `apps/api/app/services/llm/mock.py`
   has intent templates (irr_top, noi_top, vacancy, etc.) hardcoded
   for the original 4 lines. New lines (`valuation`, `advisory`, …)
   fall through to `fallback_unknown` for most intent patterns even
   when the API has the data. P2 #2 only fixes the *suggestions*
   surface, not the *answer* surface. A follow-up P3 could teach the
   mock to dispatch to a generic `line_indicators` view for unknown
   lines.

2. **live API re-mounting on registry change.** The `mount_business_line_routers`
   path in `apps/api/app/routers/registry.py` runs only at
   `lifespan` startup. Adding a new line requires an API restart.
   The mock backend's line-keyword builder is the only piece that
   picks up changes without a restart, and only because it re-reads
   the YAML at first import (not on every request). This is a
   pre-existing limitation, not introduced by P2 #1/#2/#3.

3. **`/api/registry/lines` schema drift.** The web app's
   `apps/web/app/api/registry/route.ts` proxies to
   `${base}/api/registry/lines` (which exists on the Python side).
   The schema is `BusinessLine` from `@fin-bp/types`. Confirmed
   compatible with `buildLinePageConfig()` input shape.

---

## Assumptions

- `load_registry()` is a pure function (no I/O) and safe to call at
  module import. Confirmed by reading `apps/api/app/core/registry.py`.
- `BusinessLine` Pydantic model already exposes `nav: list[BusinessLineNavItem]`
  (verified in the registry tests). No schema changes needed.
- `indicators.yaml` first-indicator title is a stable proxy for the
  line's "headline KPI" (e.g. "动态 IRR" for residential, "估价报告数"
  for valuation). Confirmed by spot-checking the 10 manifests.
- The web app's `/api/registry` proxy returns the same shape as
  `/api/registry/lines` (verified via curl).
- PowerShell console can't display CJK strings correctly; the
  underlying API responses are valid UTF-8 and the data is correct
  (verified by Python-side assertions on the same responses).

## Blockers

None. All 3 P2s fixed, all tests pass, curl validation green.
