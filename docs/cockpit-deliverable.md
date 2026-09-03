# Cockpit Deliverable — Fin BP Portal Shell + Universal UI Library

> Worker: Coder · T0.5 (cockpit shell + universal UI)
> Date: 2026-09-02
> Project root: `C:\Users\mozzi\.mavis\workspace\fin-bp-portal\`

## 1. Scope

T0 left behind the dynamic-loading skeleton: `apps/web/app/(dashboard)/layout.tsx`,
the registry API, the package layout, and stub components. This task:

1. **Wired up the cockpit shell** — the dashboard layout now (a) fetches the
   registry from the API only, (b) groups the left nav by business line,
   (c) highlights the current line via `usePathname`, and (d) shows a top bar
   with a `RoleSwitcher` placeholder and a user-menu dropdown placeholder.
2. **Replaced the three stub components** with full implementations of
   `UniversalKpiCard`, `UniversalChart`, and `EmptyState`, plus a new
   `RoleSwitcher`.
3. **Extended the registry API contract** so `/api/registry/lines` returns the
   `display_name` and `indicators_count` fields required by the cockpit.
4. **Documented the 5-step "add a business line" workflow** in
   `docs/plugin-howto.md`.
5. **Simulated a `test_line` registration** end-to-end to prove the contract.

## 2. Files changed

### apps/web (Next.js)

| File | Action | Notes |
|------|--------|-------|
| `apps/web/app/(dashboard)/layout.tsx` | rewritten | server-side fetch, delegates menu to client `SidebarMenu`, top bar to `Topbar` |
| `apps/web/app/(dashboard)/_components/SidebarMenu.tsx` | new | client component; uses `usePathname` to compute the active key, sorts by `display_name`, groups nav by line, boldens the current line in its group header |
| `apps/web/app/(dashboard)/_components/Topbar.tsx` | new | client component; hosts `RoleSwitcher` + user menu dropdown (both UI placeholders) |
| `apps/web/app/(dashboard)/dashboard/page.tsx` | rewritten | business-line card grid: icon + display_name + description + indicator count + version; empty state uses the new `EmptyState` with a docs CTA |

`apps/web/app/page.tsx`, `apps/web/lib/registry.ts`, `apps/web/app/api/registry/route.ts` were
already correct from T0 and were **not** changed.

### apps/api (FastAPI)

| File | Action | Notes |
|------|--------|-------|
| `apps/api/app/routers/registry.py` | edited | `_summarize_line()` projects a `RegistryEntry` to the cockpit shape (adds `display_name` aliasing `name` and `indicators_count = len(indicators)`); `list_lines()` now returns the projection instead of the raw `BusinessLine` model dump |
| `apps/api/tests/test_api.py` | edited | updated `test_registry_endpoint` to validate the new shape (no longer asserts `lines == []` because T1/T2 have populated the registry) and added `test_registry_endpoint_shape_keys` |
| `apps/api/tests/test_registry.py` | edited | updated `test_registry_yaml_loads` and renamed `test_load_registry_returns_empty_list_when_no_lines` → `test_load_registry_returns_list` to handle the populated registry |

### packages/types (TS)

| File | Action | Notes |
|------|--------|-------|
| `packages/types/src/index.ts` | edited | added optional `display_name` and `indicators_count` fields to `BusinessLine` (optional for backward compat) |

### packages/ui (TS)

| File | Action | Notes |
|------|--------|-------|
| `packages/ui/src/UniversalKpiCard.tsx` | rewritten | accepts `indicator: { id, name, unit?, format? }`; renders value formatted per `format` (currency/number/percent/ratio), an inline-SVG sparkline, and a `delta` (period-over-period ratio) or `trend` (string shortcut) with up/down/flat arrow + colour |
| `packages/ui/src/UniversalChart.tsx` | rewritten | ECharts 5 factory; type-driven — `line`, `bar`, `scatter` (with `size` → symbolSize mapping), `waterfall` (auto-detects negative values and `isSubtract` markers; computes the placeholder + delta stacks; optional total bar), `heatmap` (matrix + xCategories + yCategories + visualMap) |
| `packages/ui/src/EmptyState.tsx` | rewritten | renders antd `Empty` with title + description + an optional docs CTA button that links to `docsHref` (used by the dashboard to point at `plugin-howto.md`) |
| `packages/ui/src/RoleSwitcher.tsx` | new | UI-only placeholder dropdown for the four requested roles (Admin / BP-Residential / BP-Retail / BusinessHead); no auth wiring |
| `packages/ui/src/index.ts` | edited | exports the new components and types |

### docs

| File | Action | Notes |
|------|--------|-------|
| `docs/plugin-howto.md` | new | 5-step walk-through (copy template → edit manifest + indicators → wire api + page → register in `registry.yaml` → restart) + auto-discovery diagram + do/don't list |
| `docs/cockpit-deliverable.md` | new | this file |

### business_lines (simulated registration only — now cleaned up)

| File | Action | Notes |
|------|--------|-------|
| `business_lines/registry.yaml` | edited (then reverted) | temporarily appended `- id: test_line` to verify the registry contract end-to-end; reverted to the T1+T2 state (`residential`, `retail`) |
| `business_lines/test-line/` | created (then moved) | temporary copy of `_template` with `id: test_line` in its manifest, to prove the loader works; **moved out** of `business_lines/` to `_test-line-staging/` at the project root (see §6) |

## 3. Validation

### 3.1 TypeScript typecheck

```text
$ cd apps/web && npm run typecheck
> @fin-bp/web@0.1.0 typecheck
> tsc --noEmit
(no output — exit 0)
```

### 3.2 API pytest

```text
$ cd apps/api && python -m pytest -q
..........                                                                [100%]
============================== warnings summary ===============================
..\..\..\..\..\AppData\Local\Programs\Python\Python312\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\mozzi\AppData\Local\Programs\Python\Python312\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.readthedocs.io/en/stable/how-to/capture-warnings.html
========================= 9 passed, 1 warning in 0.46s =========================
```

9 tests collected, 9 passed (1 unrelated httpx deprecation warning from starlette).

### 3.3 Live API: `GET /api/registry/lines` (baseline, T1+T2 only)

```text
$ curl http://127.0.0.1:8768/api/registry/lines
{
  "version": "0.1.9720c1de",
  "lines": [
    { "id": "residential", "name": "...", "display_name": "...", "icon": "HomeOutlined",
      "indicators_count": 10, "nav": [...], "api_prefix": "/api/lines/residential", ... },
    { "id": "retail",      "name": "...", "display_name": "...", "icon": "ShopOutlined",
      "indicators_count": 8,  "nav": [...], "api_prefix": "/api/lines/retail",      ... }
  ]
}
```

Each line summary carries the contract keys: `id, name, display_name, icon,
indicators_count, nav, api_prefix` (plus the full BusinessLine payload).
`indicators_count` is the actual count of indicators defined in
`indicators.yaml` for that line.

### 3.4 Live API: simulation of `test_line` registration

1. `cp -r business_lines/_template business_lines/test-line` (then renamed
   `*.example` files to their real names).
2. Edited `business_lines/test-line/manifest.yaml` to set `id: test_line`,
   `name: "Test Line (cockpit simulation)"`, `api_prefix: /api/lines/test_line`,
   `icon: ExperimentOutlined`.
3. Appended `- id: test_line, manifest: business_lines/test-line/manifest.yaml`
   to `business_lines/registry.yaml`.
4. Restarted the API (`uvicorn --port 8768`).
5. Startup log:
   ```text
   2026-09-02T15:02:17 INFO [app.routers.registry] Mounted business line 'residential' (APIRouter) at /api/lines/residential
   2026-09-02T15:02:17 INFO [app.routers.registry] Mounted business line 'test_line' (APIRouter) at /api/lines/test_line
   ```
6. `GET /api/registry/lines` now returns **3** lines (residential, retail,
   test_line) with `LINE_IDS: ["residential","retail","test_line"]` and the
   `test_line` summary:
   ```json
   {
     "id": "test_line",
     "name": "Test Line (cockpit simulation)",
     "display_name": "Test Line (cockpit simulation)",
     "icon": "ExperimentOutlined",
     "indicators_count": 2,
     "nav": [ { "path": "/test-line", "title": "Overview" } ],
     "api_prefix": "/api/lines/test_line"
   }
   ```
7. `GET /api/registry/lines/test_line` returns the full payload (line + 2
   indicators + 1 chart) confirming the loader also resolves the per-line
   detail endpoint.
8. `GET /api/lines/test_line/ping` returns `{"status":"ok","line":"change-me"}`
   — proves the line's `api/router.py` is mounted under `api_prefix` by
   the dynamic loader.

After verification, the simulation was cleaned up (see §6).

## 4. Key design choices

1. **Layout never imports a business line** — only `BusinessLine` from
   `@fin-bp/types` and the API response. Verified by grep: only literal
   `business_lines/registry.yaml` strings appear, and only inside
   user-facing help text in the empty-state alerts.
2. **Client/server split** — the layout remains a server component (data
   fetch + render shell) while the menu and the top bar are extracted to
   `SidebarMenu.tsx` / `Topbar.tsx` client components. This is the
   minimum surface that needs `usePathname` / `useState`.
3. **Grouped sidebar** — flat menus make "current line highlight" feel
   arbitrary. The sidebar is grouped by business line, with the current
   line's group label bolded and a small caret next to its name. The
   `Overview` entry sits at the top and is highlighted on exact match.
4. **Sort by `display_name` (zh-CN aware)** — uses
   `localeCompare("zh-Hans-CN", { sensitivity: "base" })` so Chinese line
   names order naturally.
5. **UniversalKpiCard** — accepts the new prop shape from the spec
   (`indicator: { id, name, unit, format }` + `value` + optional `delta`,
   `trend`, `sparkline`). The sparkline is an inline SVG so the card
   remains dependency-free. The trend arrow + colour follow the convention
   used by antd's own `<Statistic />`.
6. **UniversalChart** — type-driven factory over ECharts 5. Each type
   accepts a `data` shape that is intentionally loose (either a flat
   `points` array or a `{ categories, values, ... }` object) and a small
   `options` escape hatch for free-form ECharts overrides via
   `options.echartsOverrides`. The waterfall normalizer auto-detects
   subtract bars (negative values OR `isSubtract: true` markers) and
   computes the placeholder + delta stacks in one pass.
7. **EmptyState** — antd `Empty` with a `docsHref` prop that, when
   provided, renders a primary button linking to the docs. The dashboard
   page uses this to point at `docs/plugin-howto.md`.
8. **RoleSwitcher** — UI-only placeholder. No auth wiring. Exposes
   `value` / `defaultRole` / `onChange` so a future real auth provider can
   drop in without changing the call sites.
9. **Registry response shape** — `_summarize_line` projects each
   `RegistryEntry` to `{id, name, display_name, version, ..., icon, nav,
   api_prefix, ..., indicators_count}`. The `display_name` field is
   introduced as a stable, i18n-ready hook (currently aliasing `name`).
   The full BusinessLine payload is still available at
   `/api/registry/lines/{line_id}` for the detail page.
10. **`apps/web/lib/registry.ts` and `apps/web/app/api/registry/route.ts`**
    were left untouched — they are still the canonical client-side fetch
    helper and same-origin proxy, and the new layout uses them via the
    server-side fetch path against the API base URL.

## 5. Assumptions

1. **Role names contain "residential" / "retail"** — the task explicitly
   requested `Admin / BP-Residential / BP-Retail / BusinessHead`. The
   constraint "通用组件 不耦合 任何业务线特有概念" is interpreted as
   "no coupling to a *specific* business line's data, schema or
   identifiers", which role labels do not violate. The two strings
   appear only in the role-name literal (not in any business-line
   registry lookup, manifest, or page). The role switcher is a UI
   placeholder; a real auth provider will replace the labels.
2. **The T1 + T2 workers' lines (`residential`, `retail`) are present
   during my work window** — this meant the T0 tests that assumed
   `lines == []` had to be updated. I left the assertions of
   "registry.yaml is a dict with a `lines` key" intact and only removed
   the `lines == []` assumption. The test now validates the *contract*
   (every line summary has the cockpit-required fields) rather than the
   *emptiness* of the registry.
3. **`usePathname` highlights via group label boldening** — antd Menu
   has no first-class "highlight a group label" API, so the current
   line is shown with a bolder font + caret in the group header. The
   matched child item still receives the standard antd-selected style.
4. **Sparkline is inline SVG, not ECharts** — the spec describes the
   sparkline as a "mini" chart, and a 28px-tall polyline is the lightest
   possible implementation. The card remains dependency-free at that
   pixel budget; if higher fidelity is needed later, a separate
   `<UniversalSparkline>` can be carved out without touching this file.
5. **No production CORS / auth** — out of scope per the task. The
   `CORSMiddleware` is permissive in dev, and the same-origin proxy
   route in `apps/web/app/api/registry/route.ts` is the recommended
   dev path.
6. **The Chinese text rendered by T1/T2's lines is rendered correctly
   in the UI** — the API and the Next.js layout both treat strings as
   opaque UTF-8. The PowerShell test pipeline shows garbled bytes
   because the local PowerShell session uses the ANSI code page by
   default, but the API itself returns valid UTF-8 (the registry file
   loader opens with `encoding="utf-8"`).

## 6. Blockers / limitations

1. **Hard-safety policy blocks `Remove-Item -Recurse -Force` and the
   `mavis-trash.cmd` wrapper for the test-line cleanup.** Per the system
   policy, no delete command can be issued from this agent's bash
   tool. The temporary `business_lines/test-line/` directory was
   therefore moved out of `business_lines/` to `_test-line-staging/` at
   the project root, so the registry loader no longer picks it up
   (verified by `GET /api/registry/lines` returning only `residential`
   and `retail` after the move). The directory is still on disk and
   can be moved to the Recycle Bin manually.
2. **Workspace is a reparse point** — `C:\Users\mozzi\.mavis\workspace\`
   redirects to `C:\Users\mozzi\.minimax\workspace\`. The API log
   prints the resolved root (`C:\Users\mozzi\.minimax\workspace\...`).
   Tools that refuse reparse paths need the resolved path.
3. **AG Grid CSS** — `packages/ui/src/UniversalAgGrid.tsx` already
   imports the quartz theme CSS, so it works anywhere it's used, but no
   page in `apps/web` imports it yet. T1/T2 may add grid pages that
   import it.
4. **`docker` is not installed on the worker host** — the infra
   `docker-compose.yml` was structurally validated in T0 but cannot be
   brought up here. The API used an in-process TestClient + a real
   uvicorn process against the existing files; the warehouse backends
   (Postgres / ClickHouse / MinIO) were not exercised.
5. **No automated browser test for the Next.js shell.** I typechecked
   the project and exercised the API end-to-end, but did not start
   `next dev` and drive the dashboard in a real browser. The server
   component is straightforward (single fetch + JSX) and the client
   components are unit-test-friendly; a follow-up could add Playwright
   tests for the sidebar highlight behaviour.

## 7. Step count of the plugin how-to

`docs/plugin-howto.md` has exactly **5 numbered steps** (0 is the
overview diagram, 1-5 are the action steps), matching the "≤ 5 steps"
acceptance criterion.
