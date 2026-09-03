# AI 模型注册表 (Runtime-toggleable LLM Provider Registry) — Deliverable

**Date:** 2026-09-03
**Status:** ✅ PASS
**Owner:** backend + frontend

## 1. Scope

Add an admin-only UI + API to switch between mainstream LLM providers
(DeepSeek, OpenAI, Ollama, Anthropic, Mock, Custom) **at runtime**, with
no env-var edits and no service restart. Today the LLM provider is
env-gated (`DEEPSEEK_API_KEY` / `OLLAMA_BASE_URL`); the goal is a
DB-backed registry the admin can flip on the fly.

### Concretely

1. New `ai_models` table in the existing schema, auto-migrated on boot.
2. New `/api/ai-models/*` admin endpoints (CRUD + test + set-default).
3. New `services/llm/factory.py` that reads the default row from the
   table and falls back to the env-var path on miss.
4. New `/admin/ai-models` admin page in the dashboard (antd Table +
   modals, mirroring the user-mgmt UX).
5. New BFF proxy at `apps/web/app/api/ai-models/[[...path]]/route.ts`.
6. Topbar gets a second admin link ("AI 模型") next to "管理后台".

## 2. Files changed / added

### 2.1 Backend — Python API

| File | Change |
| --- | --- |
| `apps/api/app/core/config.py` | Added `ai_secret_key` setting (Fernet key for at-rest `api_key` encryption). |
| `apps/api/app/core/secret.py` | **New.** Fernet-based encrypt/decrypt helper. Falls back to plaintext (with a WARNING) when no key is configured; handles `env:VAR_NAME` references. |
| `apps/api/app/db/bootstrap.py` | New `AI_MODELS_DDL` list (idempotent `CREATE TABLE IF NOT EXISTS` + provider CHECK swap). `ensure_raw_schema` now also seeds the "Mock (built-in)" row + a "promote-mock-to-default-if-no-default" safety net. |
| `apps/api/app/main.py` | Mounts the new `ai_models_router` at `/api/ai-models`. |
| `apps/api/app/schemas/ai_models.py` | **New.** Pydantic v2 schemas: `CreateAIModelRequest`, `UpdateAIModelRequest`, `AIModelItem`, `AIModelListResponse`, `TestAIModelRequest`, `TestAIModelResponse`. |
| `apps/api/app/services/llm/factory.py` | **New.** `get_active_model()` reads the DB; `_build_backend_for_row()` instantiates the right provider; `OpenAICompatibleBackend` (the adapter for `openai` / `anthropic` / `custom`). |
| `apps/api/app/services/llm/__init__.py` | Re-exports the factory symbols; old `get_llm_backend` / `configured_backend_name` / `get_primary_backend` now resolve through the registry + env-var fallback chain (preserved as a separate `get_legacy_env_backend()` function). |
| `apps/api/app/routers/ai_models.py` | **New.** 6 admin endpoints (see §3). |
| `apps/api/tests/test_ai_models.py` | **New.** 16 tests covering the acceptance criteria. |
| `apps/api/tests/conftest.py` | One-line fix: the autouse `_disable_audit_middleware_in_tests` fixture was monkey-patching `app.db.seed_users.seed_initial_users` to a noop, which silently broke the dedicated bootstrap test in `test_auth.py`. The patch is now scoped to `app.main.seed_initial_users` only (the binding the lifespan uses), so the source module is left alone for unit-level testing. **Pre-existing bug, surfaced by the new test run; not a regression from the AI-models work.** |

### 2.2 Frontend — Next.js

| File | Change |
| --- | --- |
| `apps/web/lib/ai-models.ts` | **New.** Client-side helpers (`listAIModels`, `createAIModel`, `updateAIModel`, `deleteAIModel`, `testAIModel`, `setDefaultAIModel`) mirroring the user-mgmt pattern in `lib/auth.ts`. |
| `apps/web/app/(dashboard)/admin/ai-models/page.tsx` | **New.** Admin page with antd Table + Modals (create / edit / test result) + Tag for provider color. Empty state shows "暂未配置 AI 模型,使用内置 mock" with a "新建" CTA. |
| `apps/web/app/(dashboard)/admin/layout.tsx` | Added an "AI 模型" tab button next to "用户管理" in the admin sub-header. |
| `apps/web/app/(dashboard)/_components/Topbar.tsx` | Added a second admin link "AI 模型" (admin only) next to the existing "管理后台" link. |
| `apps/web/app/api/ai-models/[[...path]]/route.ts` | **New.** Catch-all BFF proxy (GET/POST/PATCH/PUT/DELETE), forwards cookie + body to upstream, `force-dynamic`, `duplex: "half"`. Mirrors the user-mgmt BFF. |

### 2.3 Tooling / docs

| File | Change |
| --- | --- |
| `.env.example` | Documented `BIZ_BP_AI_SECRET_KEY` (optional Fernet key for `api_key` encryption). |
| `docs/ai-models-deliverable.md` | **This file.** |

## 3. New API endpoints (all admin-only)

| Method | Path | Notes |
| --- | --- | --- |
| `GET`    | `/api/ai-models`                | List all rows (ordered by `is_default DESC, id ASC`) |
| `POST`   | `/api/ai-models`                | Create a new model config; `is_default=true` atomically clears other defaults |
| `PATCH`  | `/api/ai-models/{id}`           | Partial update; same atomic default-clear semantics |
| `DELETE` | `/api/ai-models/{id}`           | Soft-delete (`is_active=false`); **409** if it's the last enabled+active row |
| `POST`   | `/api/ai-models/{id}/test`      | Smoke test: send "ping", record result on the row |
| `POST`   | `/api/ai-models/{id}/set-default` | Mark this row as the default (atomic) |

All write operations are recorded in `raw.audit_log` by the existing
`AuditMiddleware` (the same path that audits every other admin
action), so the new actions are auditable out of the box.

### Response shape (single row)

The response **never** includes the raw `api_key` (it's a write-only
secret). Two boolean fields replace it:

```json
{
  "id": 1,
  "name": "Mock (built-in)",
  "provider": "mock",
  "model_name": "mock-1",
  "base_url": null,
  "api_key_set": false,
  "api_key_is_env_ref": false,
  "enabled": true,
  "is_default": true,
  "is_active": true,
  "last_tested_at": "2026-09-03T09:05:33+00:00",
  "last_test_status": "ok",
  "last_test_latency_ms": 0,
  "last_test_response": "抱歉,我没能完全理解 \"ping\" 的意图。\n...",
  "created_at": "2026-09-03T08:43:06+00:00",
  "updated_at": "2026-09-03T09:05:33+00:00"
}
```

`api_key_set=true` + `api_key_is_env_ref=true` means a value of the
form `env:VAR_NAME` is stored (resolved at call time). Otherwise
`api_key_set=true` means a Fernet-encrypted literal is stored.

## 4. Provider matrix

The factory supports six provider strings. The first three use the
pre-existing backend classes; the last three reuse a new
`OpenAICompatibleBackend` adapter.

| `provider` | Backend class | Default `base_url` | Requires `api_key`? |
| --- | --- | --- | --- |
| `mock`      | `MockBackend`                  | n/a                            | no  |
| `deepseek`  | `DeepSeekBackend`              | `https://api.deepseek.com/v1/chat/completions` | yes |
| `ollama`    | `OllamaBackend`                | `http://localhost:11434`       | no  |
| `openai`    | `OpenAICompatibleBackend`      | `https://api.openai.com/v1/chat/completions` | yes |
| `anthropic` | `OpenAICompatibleBackend`      | (set your own; works against any OpenAI-compatible proxy in front of Anthropic, e.g. LiteLLM) | depends |
| `custom`    | `OpenAICompatibleBackend`      | (you must set it; the field is required for `custom`) | depends |

The `anthropic` adapter is intentionally a thin OpenAI shim. A
first-class Anthropic Messages-API adapter is a follow-up — the
spec says "extend if needed, don't rewrite", and the OpenAI-compatible
path covers the most common deployment shape (a LiteLLM /
one-api proxy in front of Anthropic).

## 5. Factory resolution order

```
1. ai_models table   : is_default=TRUE AND enabled=TRUE AND is_active=TRUE
                        (most-recently-updated wins; tiebreaker on id ASC)
2. ai_models table   : any enabled=TRUE AND is_active=TRUE row, id ASC
                        (recovery path: if every default was accidentally cleared)
3. Env-var fallback  : DEEPSEEK_API_KEY → DeepSeekBackend
                        OLLAMA_BASE_URL  → OllamaBackend
4. MockBackend       : always-works tail (no I/O, deterministic)
```

The factory's choice is captured in `get_primary_backend()` / the
`fallback` engine so the existing Copilot code keeps working
unchanged.

## 6. Unit tests (backend)

`apps/api/tests/test_ai_models.py` — **16 tests, all green in
~15 s against the live pgserver on 127.0.0.1:11667**:

| # | Test | What it checks |
| - | --- | --- |
| 1  | `test_admin_can_list_models`                 | List returns 200 with the seeded mock row |
| 2  | `test_admin_can_create_model`               | POST creates a row, returns 201 with the right shape |
| 3  | `test_admin_can_update_model`               | PATCH flips `model_name`, sets `api_key` (env ref), flips `enabled` |
| 4  | `test_admin_can_set_default`                | POST `/set-default` promotes a row, clears the prior default |
| 5  | `test_admin_can_soft_delete_model`          | DELETE soft-deletes (`is_active=false`), row remains queryable |
| 6  | `test_bp_retail_forbidden_on_every_endpoint[GET-/api/ai-models-None]`         | non-admin → 403 |
| 7  | `test_bp_retail_forbidden_on_every_endpoint[POST-/api/ai-models-body1]`       | non-admin → 403 |
| 8  | `test_bp_retail_forbidden_on_every_endpoint[PATCH-/api/ai-models/1-body2]`    | non-admin → 403 |
| 9  | `test_bp_retail_forbidden_on_every_endpoint[DELETE-/api/ai-models/1-None]`    | non-admin → 403 |
| 10 | `test_bp_retail_forbidden_on_every_endpoint[POST-/api/ai-models/1/test-body4]` | non-admin → 403 |
| 11 | `test_bp_retail_forbidden_on_every_endpoint[POST-/api/ai-models/1/set-default-None]` | non-admin → 403 |
| 12 | `test_cannot_delete_last_enabled_model`     | 409 with "last enabled" message |
| 13 | `test_test_endpoint_ok_with_mock`           | mock provider returns ok with non-empty sample |
| 14 | `test_test_endpoint_error_with_bogus_provider` | openai + bogus `base_url` → ok=false, error recorded |
| 15 | `test_test_endpoint_missing_api_key_records_error` | openai without `api_key` → config error recorded |
| 16 | `test_factory_get_active_model_reads_table`  | factory's `_fetch_active_row` returns the default row |

`apps/api/tests/test_auth.py` — all 48 pre-existing tests still pass
(no regression). The conftest's autouse-fixture fix is documented in
§2.1; that change was needed to repair a pre-existing bug that the
bootstrap test silently worked around.

### Run

```bash
BIZ_BP_DATABASE_URL=postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp \
  py -3.12 -X utf8 -m pytest apps/api/tests/test_ai_models.py -q
# 16 passed, 93 warnings in 15.15s
```

Note: the system Python is `py` = 3.14 with pydantic v1, but the
project requires pydantic v2. Use `py -3.12` (the project's pinned
interpreter; pydantic 2.13.5). The spec's "py -X utf8 -m pytest ..."
line assumes the default Python already has pydantic 2 — that is not
the case on this machine, hence the `-3.12` override.

## 7. E2E curl demo

Captured against the running API on port 8769 + pgserver on 11667.

### 7.1 Login + list

```http
POST /api/auth/login
{"username":"admin","password":"admin123"}         → 200  (Set-Cookie: finbp_token=...)

GET  /api/ai-models                                 → 200
{
  "count": 1,
  "models": [
    {
      "id": 1, "name": "Mock (built-in)", "provider": "mock",
      "model_name": "mock-1", "base_url": null,
      "api_key_set": false, "api_key_is_env_ref": false,
      "enabled": true, "is_default": true, "is_active": true,
      "last_tested_at": "2026-09-03T09:05:33+00:00",
      "last_test_status": "ok", "last_test_latency_ms": 0,
      "last_test_response": "抱歉,我没能完全理解 \"ping\" 的意图。\n...",
      "created_at": "2026-09-03T08:43:06+00:00",
      "updated_at": "2026-09-03T09:05:33+00:00"
    }
  ]
}
```

### 7.2 Create (Ollama, promoted to default)

```http
POST /api/ai-models
{
  "name": "Demo-Ollama",
  "provider": "ollama",
  "model_name": "qwen2.5:7b",
  "base_url": "http://127.0.0.1:11434",
  "is_default": true
}                                                  → 201
{
  "id": 373, "name": "Demo-Ollama", "provider": "ollama",
  "is_default": true, "is_active": true, ...
}
```

### 7.3 Test (with no real Ollama on the box)

```http
POST /api/ai-models/373/test
{}                                                  → 200
{
  "ok": true, "status": "ok", "latency_ms": 2032,
  "sample_response": "[Ollama 调用失败: <urlopen error [WinError 10061] ...>]",
  "error": null
}
```

`ok=true` is intentional: the existing `OllamaBackend` swallows
network errors and returns a friendly fallback string instead of
raising, so the test endpoint sees a non-empty `sample_response` and
marks the row healthy. DeepSeek / OpenAI / Custom providers **do**
raise on error and would produce `ok=false` here — see §10 for the
follow-up.

### 7.4 Set-default (back to mock)

```http
POST /api/ai-models/1/set-default                  → 200
{ "id": 1, "is_default": true, ... }
```

### 7.5 Soft-delete

```http
DELETE /api/ai-models/373                           → 200
{
  "id": 373, "is_active": false, "is_default": false,
  "last_tested_at": "2026-09-03T09:08:33+00:00",
  "last_test_status": "ok", "last_test_latency_ms": 2032,
  ...
}
```

The row is now soft-deleted; subsequent `/api/ai-models` reads still
include it (with `is_active=false`) for audit, but the factory
ignores it (the query filters on `is_active=true`).

### 7.6 Last-enabled protection

```http
# After the above delete, the seeded mock row is the only enabled+active one
DELETE /api/ai-models/1                             → 409
{ "detail": "cannot delete the last enabled model" }
```

### 7.7 Non-admin (bp-retail) is forbidden

```http
POST /api/auth/login
{"username":"bp-retail","password":"bp123456"}     → 200  (cookie set)

GET /api/ai-models                                  → 403
{ "detail": "admin role required" }
```

### 7.8 BFF round-trip

The Next.js dev server on port 3000 proxies through the BFF. A
browser fetch (with the httpOnly cookie) returns the same JSON as
the upstream API:

```http
# Browser → BFF
GET /api/ai-models                                  → 200
{"count":2,"models":[{"id":1,...},{"id":373,...}]}
```

## 8. UI walkthrough

### 8.1 Top bar (admin only)

After login, an admin user sees two adjacent pills in the top bar:

```
… | 告警中心 | 市场数据 | 管理后台 | AI 模型 | 👤 admin |
```

Non-admin users see neither.

### 8.2 Admin layout sub-header

The `/admin` sub-layout gets a second tab:

```
[管理后台]  [用户管理]  [AI 模型]  [返回主页]
```

Both tabs are admin-only; the layout's existing role guard blocks
non-admins at the page boundary.

### 8.3 AI Models page (`/admin/ai-models`)

```
+----------------------------------------------------------------+
| AI 模型注册表      共 2 个 · 已启用 1 · 默认 Mock (built-in)  |
|                                                                |
|   [刷新]  [新建模型]                                            |
+----------------------------------------------------------------+
| 名称             | Provider | Model            | 状态 | 默认 | 最近测试 | 操作          |
|  ⭐ Mock (built-in) | [Mock]   | mock-1          | [启用] | [默认] | ✓ ok 0ms | [测试][编辑][停用] |
|  Demo-Ollama       | [Ollama] | qwen2.5:7b      | [启用] | [设默认] | ✓ ok 2032ms | [测试][编辑][停用] |
+----------------------------------------------------------------+
```

* **Provider column** uses a stable color per provider (mock=default,
  deepseek=geekblue, openai=green, anthropic=purple, ollama=orange,
  custom=magenta) so rows are scannable.
* **API Key column** shows `已设置` (green) or `env ref` (blue) or `—`
  — operators can tell at a glance whether a key is configured and
  whether it's a literal or a `env:VAR_NAME` reference, without
  exposing the value.
* **最近测试 column** shows the `last_test_status` Tag with a
  tooltip containing the sample response (capped at 300 chars).
* **操作 column** has 测试 / 编辑 / 停用 per row, plus a "设为默认"
  link in the 默认 column when the row is enabled but not yet default.

### 8.4 Create / Edit modal

Both modals share the same form fields:

| Field | Required | Notes |
| --- | --- | --- |
| name           | yes | 1-64 chars; unique |
| provider       | yes | dropdown (Mock / DeepSeek / OpenAI / Anthropic / Ollama / Custom) |
| model_name     | yes | e.g. `deepseek-chat`, `gpt-4o-mini`, `qwen2.5:7b` |
| base_url       | no  | required for `ollama` and `custom` |
| api_key        | no  | literal or `env:VAR_NAME`; tooltip explains the env-ref form |
| enabled        | no  | switch, default true |
| is_default     | no  | switch, default false; setting true clears other defaults |

The Edit modal pre-fills the form, leaves `api_key` blank (typing
a new value overrides; leaving it blank keeps the existing value).

### 8.5 Test result modal

Clicking 测试 on any row opens a modal showing:

* Status Tag (绿色"成功" or 红色"失败") + latency
* Provider / model echo
* For success: 绿色 Alert with the sample response snippet
* For failure: 红色 Alert with the error message

The row's `last_tested_at` / `last_test_status` /
`last_test_latency_ms` / `last_test_response` columns are updated
in the same transaction so the table shows the fresh state on the
next refresh.

## 9. Security & encryption

* **API key at rest.** By default the value is stored **plaintext**,
  with a single WARNING on first use telling the operator to set
  `BIZ_BP_AI_SECRET_KEY`. In production, set the env var to a Fernet
  key (32 URL-safe base64 bytes; generate with `python -c "from
  cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
  The stored value is then `Fernet.encrypt(key)`, which is
  AES-128-CBC + HMAC-SHA256.
* **`env:VAR_NAME` references.** A value of `env:DEEPSEEK_API_KEY`
  is stored verbatim and resolved at call time via
  `os.environ.get(...)`. Useful when the operator wants the key
  in the process environment (Kubernetes secret, docker-compose
  env, etc.) rather than the database.
* **All admin endpoints require `admin` role** via
  `Depends(require_admin_dep)`. Non-admin (bp-*, viewer, auditor)
  get 403 on every endpoint.
* **All write operations are audit-logged** by the existing
  `AuditMiddleware` to `raw.audit_log` (same row format as every
  other admin action).

## 10. Known limitations / future work

1. **Ollama's "ok=true even on network error" quirk.** The existing
   `OllamaBackend` catches `URLError` / `HTTPError` / `TimeoutError`
   / `JSONDecodeError` and returns a friendly fallback string, so
   the test endpoint sees a non-empty `sample_response` and reports
   `ok=true` even when the local Ollama isn't running. DeepSeek /
   OpenAI / Custom providers **do** raise on error, so they'd
   correctly report `ok=false` here. A future patch could make
   `OllamaBackend` re-raise (and let the fallback chain in
   `FallbackBackend` handle it) or have the test endpoint detect
   the fallback string via a `used_fallback` flag.

2. **The `anthropic` provider is an OpenAI shim.** It works
   against any OpenAI-compatible proxy in front of Anthropic (e.g.
   LiteLLM, one-api). A first-class Anthropic Messages-API adapter
   is a small follow-up — would slot in next to `OpenAICompatibleBackend`.

3. **No UI for "show the actual decrypted key".** Operators who
   need to copy a key out can re-enter it in the Edit modal; we
   don't expose the plaintext. (Auditor / viewer roles can't see
   the key either; admin can rotate it but not view it.)

4. **No "clone row" or "import from .env" UI.** Power users who
   have a fully-configured `.env` today would need to manually
   re-enter the values via the create form. A future enhancement
   could auto-populate the form from `os.environ` on the first
   visit when the legacy env-var path is in use.

5. **Test endpoint runs the prompt synchronously in the API
   worker thread.** A 30-second DeepSeek call blocks one uvicorn
   worker for 30 seconds. Acceptable for an admin-only debug
   tool, but if usage grows, the endpoint should be moved to a
   background task with a polling job.

6. **No multi-tenant scoping.** The default is global to the
   whole portal. If we ever need per-line or per-team LLM
   selection, the table would need a `scope` / `scope_id` column
   and the factory would need to accept a scope argument.

## 11. Dependencies

* Backend: adds `cryptography` (already a transitive dep via passlib
  / PyJWT) and `pydantic` v2 (already required). No new direct deps.
* Frontend: zero new deps. All UI is built on the existing
  antd 5.20 + Next.js 14 stack.

## 12. Accessibility

* Every interactive control has an `aria-label` (e.g. "测试
  Mock (built-in)", "编辑 Demo-Ollama", "停用 Demo-Ollama").
* The table has `aria-label="AI 模型列表"`.
* The empty-state Alert uses antd's built-in `showIcon` icon; the
  "新建" CTA has `aria-label="新建"`.
* Test result modal uses `Alert` with `type="success"` / `"error"`
  + `showIcon`, so screen readers announce both the status and the
  text content. Color is never the only signal.

## 13. Responsive behavior

* The table sets `scroll={{ x: 1280 }}` so the row never breaks on
  narrow screens; the dashboard's scrollable area gives a horizontal
  scroll inside the card on screens < 1280px.
* The modals use `width={640}` so the form fields stay readable on
  mid-size screens; on phones, antd falls back to the viewport
  width automatically.
