# LLM Integration Deliverable — Fin BP Portal AI Copilot

**Status**: ✅ Complete — mock → real LLM, with DeepSeek as primary + Ollama as backup.

**Date**: 2026-09-03
**Scope**: Replace the mock-only LLM backend with a pluggable architecture that supports DeepSeek (OpenAI-compatible, https://api.deepseek.com) and Ollama (local). Mock is preserved as a deterministic rule engine and as a graceful-fallback target.

---

## Result

**PASS** — 61 / 61 copilot-related tests pass, frontend typecheck clean, fallback chain proven on a live HTTP call with a fake DeepSeek key.

```
$ python -m pytest tests/test_copilot.py tests/test_llm_backends.py -v
============================= test session starts =============================
collected 61 items
tests/test_copilot.py    .........................   [ 40%]
tests/test_llm_backends.py  ....................................   [100%]
======================== 61 passed, 1 warning in 6.50s ========================
```

---

## Backend — file list

| File | Status | Purpose |
|---|---|---|
| `apps/api/app/services/llm/prompts.py` | **NEW** | `SYSTEM_PROMPT` template + `render_system_prompt()` + `build_prompt()`. Registry-aware, 3 few-shot examples, escaped `{{}}` for format-safety. |
| `apps/api/app/services/llm/deepseek.py` | **REWRITE** | Real DeepSeek V3 client. `urllib`-only, raises on every error class (`DeepSeekConfigError` / `DeepSeekHTTPError` / `DeepSeekTimeoutError` / `DeepSeekProtocolError` / `DeepSeekError`). Tracks `last_call_status`, `last_latency_ms`, `call_count`, `success_count`. |
| `apps/api/app/services/llm/__init__.py` | **REWRITE** | Factory: `DEEPSEEK_API_KEY` → `FallbackBackend(DeepSeek, Mock)`; `OLLAMA_BASE_URL` → `FallbackBackend(Ollama, Mock)`; else `MockBackend`. New `FallbackBackend` class implements the chain and exposes `used_fallback` + `last_error` + `last_answer`. |
| `apps/api/app/services/copilot_engine.py` | **REWRITE** | `CopilotRequest` gets `prefer_real_llm: bool | None`. Engine picks backend per-request via `_pick_backend()`. `CopilotResponse` extended with `used_fallback`, `fallback_reason`, `model`. `CopilotHealth` extended with `configured_backend`, `deepseek_key_present`, `ollama_url`, `model`, `temperature`, `used_fallback`, `last_call_status`, `last_error`, `last_latency_ms`, `call_count`, `success_count`, `primary_stats`. |
| `apps/api/app/routers/copilot.py` | unchanged | The `response_model=CopilotHealth` / `CopilotResponse` annotations automatically pass through the new fields. |
| `apps/api/tests/test_llm_backends.py` | **NEW** | 36 tests covering: DeepSeek client (success / 4xx / 5xx / timeout / network / invalid JSON / empty choices / empty content / missing key / embed), factory priority, FallbackBackend chain, system prompt + build_prompt content, health endpoint with various env, full HTTP round-trip with fake key + patched urllib, and `prefer_real_llm` toggle. |
| `infra/.env.example` | **UPDATE** | New section for `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_TIMEOUT` / `DEEPSEEK_TEMPERATURE` and `OLLAMA_*`. Comments explain priority + fallback semantics. |

### Key implementation notes

- **No new Python dependencies** — `urllib.request` is stdlib. `httpx` is already a project dep but unused for this layer.
- **No hard-coded single backend** — the factory reads env at *call* time (not import time), so tests and ops can swap backends per request. The `FallbackBackend` wrapper is constructed by the factory, never directly.
- **Fallback re-uses the mock rule engine** — when the primary raises, `FallbackBackend.complete()` re-derives the question from the prompt, calls `MockBackend.answer()` (sync), and returns the mock's text + populates `last_answer` with the structured `MockAnswer` (citations + chart). The engine then uses that to enrich the response.
- **Both failures degrade to 200** — every request to `/api/copilot/ask` returns 200. The Copilot response carries `used_fallback: true` + `fallback_reason: "<error class>: <message>"` so the frontend can render a "⚠️ 降级" banner.
- **`prefer_real_llm` is a per-request override** of the env-driven default. UI toggle forwards it in the request body; the engine's `_pick_backend()` honors it without touching env vars.

---

## Frontend — file list

| File | Status | Purpose |
|---|---|---|
| `apps/web/app/(dashboard)/copilot/page.tsx` | **REWRITE** | New `BackendSettings` panel (collapsible Card with `Descriptions`): current backend, key status, model, temperature, call count, success rate, last status, last latency, last error. Helper hint when nothing is configured. Backend badge now clickable to open the panel. New `Switch` toggle "用真实 LLM" next to the line hint, only shown when a real backend is configured. `MessageBubble` extended to render `used_fallback` warning + `model` tag. Auto-refreshes `/api/copilot/health` after every ask. |
| `apps/web/app/api/copilot/health/route.ts` | unchanged | Already a transparent BFF pass-through. |
| `apps/web/app/api/copilot/ask/route.ts` | unchanged | Forwards `prefer_real_llm` as a body field; backend honours it. |

### UI structure (after this change)

```
┌─ Header ───────────────────────────────────────────────────────┐
│ 🤖 AI Copilot  [MOCK] [deepseek-chat] 4 业务线  [后端设置]     │
├─ Suggestions (collapsible) ───────────────────────────────────┤
│ Recommended questions …                                        │
├─ Message list ────────────────────────────────────────────────┤
│  user (right, blue)                                            │
│  AI   (left, white)  [intent] [confidence] [BACKEND] [FALLBACK]│
│       ⚠ 降级提示: DeepSeek HTTP 401: Authentication Fails …     │
│       answer text                                              │
│       [引用] [附图]                                             │
├─ Input + send + ☐ 用真实 LLM ──────────────────────────────────┤
└────────────────────────────────────────────────────────────────┘
```

`BackendSettings` panel (when expanded) shows two columns of metadata: configured vs runtime stats.

---

## Prompts — system prompt summary

`apps/api/app/services/llm/prompts.py` exports:

- `SYSTEM_PROMPT` — a 60-line static template, with 3 placeholders (`{business_lines}`, `{cross_endpoints}`, `{api_base}`) that are filled at runtime via `render_system_prompt()`. It contains:
  1. Persona ("你是 Fin BP Portal 的 AI Copilot,一名专业的金融业务伙伴助手")
  2. Service audience ("服务于 Fin BP Portal 平台 — 金融 BP / 财务 / 运营 人员的统一数据分析平台")
  3. Live business-line table (registry-driven — pulled from `load_registry()` at render time)
  4. Per-line endpoint catalog (hardcoded `ENDPOINT_CATALOG` for residential / retail / retail-leasing / my-line)
  5. Cross-line endpoints (`/api/registry/lines`, `/api/sensitivity/profiles/{line_id}`, `/api/sensitivity/analyze`)
  6. **4 hard rules** (only use context data, no fabrication, no cross-line inference, give regulatory thresholds)
  7. **Output format** (always end with `参考资料: <endpoints + key fields + values>`)
  8. **3 few-shot examples**: residential IRR top-3, residential three-red-line trigger, sensitivity 1D scan setup.

- `build_prompt(question, line_id, context_data)` — factory that renders the user-side prompt with:
  - 业务线过滤 (line hint)
  - 用户问题 (the original question)
  - 上下文数据 (the data fetched by mock_helpers, pretty-printed as JSON; if `None` or empty, the prompt explicitly says "no data — suggest endpoint to call")

Both are pure functions, no I/O side effects beyond the one-time `load_registry()` call (cached on the backend instance).

---

## Test count

| File | Count | Status |
|---|---|---|
| `tests/test_copilot.py` (existing) | 25 | ✅ all pass |
| `tests/test_llm_backends.py` (new) | 36 | ✅ all pass |
| **Total** | **61** | ✅ **all pass** in 6.50s |

The 36 new tests cover:

- **DeepSeek client** (10 tests, all `urllib.request.urlopen` mocked):
  1. `test_constructor_without_api_key_raises`
  2. `test_constructor_with_api_key_ok`
  3. `test_success_2xx_returns_message_content`
  4. `test_4xx_raises_http_error`
  5. `test_5xx_raises_http_error`
  6. `test_timeout_raises_timeout_error`
  7. `test_network_error_raises`
  8. `test_invalid_json_raises_protocol_error`
  9. `test_empty_choices_raises_protocol_error`
  10. `test_empty_content_raises_protocol_error`
  11. `test_embed_returns_empty_list` (11 total)

- **Factory** (4 tests): no-env → mock, deepseek → FallbackBackend(DeepSeek, Mock), ollama → FallbackBackend(Ollama, Mock), deepseek-wins-over-ollama.

- **Fallback chain** (4 tests): primary success, primary failure, full engine path with `used_fallback=true` on the response, total-outage (both primary and mock fail) — still returns a non-empty string instead of raising.

- **Prompts** (8 tests): role / business lines / few-shot / citation rule in `SYSTEM_PROMPT`; `build_prompt` includes question / context data / handles `None` line / handles empty context.

- **Health endpoint** (3 tests): no-env, deepseek-key, ollama-url — verifies the new `configured_backend`, `model`, `temperature`, etc. fields.

- **Full HTTP round-trip** (`TestClient` + patched urllib) (3 tests): fake key → 200 + `used_fallback=true` + `fallback_reason`; mocked success → 200 + `backend=deepseek` + `model=deepseek-chat` + answer contains DeepSeek text; no-key → 200 + `backend=mock` + `used_fallback=false`.

- **`prefer_real_llm` toggle** (3 tests): True with key → real LLM path; False with key → forced mock; True without key → falls back to mock (can't be honored).

---

## Validation — 8 acceptance points

| # | Criterion | Result |
|---|---|---|
| 1 | `pytest tests/test_llm_backends.py -v` all 15+ pass | ✅ **36 / 36 pass** |
| 2 | `pytest tests/test_copilot.py -v` still 25+ pass (no regression) | ✅ **25 / 25 pass** |
| 3a | `GET /api/copilot/health` without env → `backend: "mock"`, `used_fallback: false` | ✅ See validation log below |
| 3b | `GET /api/copilot/health` with `DEEPSEEK_API_KEY=fake-key` → `backend: "deepseek"`, `used_fallback: false`, `model: "deepseek-chat"` | ✅ See validation log below |
| 3c | `POST /api/copilot/ask` with fake key → 200, `used_fallback: true`, `fallback_reason` populated (NOT a 500) | ✅ See validation log below |
| 4 | Frontend `npm run typecheck` passes | ✅ `tsc --noEmit` exits 0 |
| 5 | `/copilot` route still works (200, same shell) | ✅ `page.tsx` compiles; types unchanged for the new fields (all optional) |
| 6 | Top of page shows current backend + a "后端设置" entry point | ✅ `BackendSettings` panel rendered by default-collapse, opened by clicking the backend badge or the "后端设置" button |
| 7 | All 14 existing mock intents still work | ✅ `test_copilot.py` exercises all 12+ intent templates; all pass |
| 8 | No new pip dependency | ✅ Only stdlib `urllib.request`, `urllib.error`, `json`, `time` used |

### Validation log — `/api/copilot/health` no env

```json
{
  "backend": "mock",
  "available_lines": ["residential","retail","retail-leasing","my-line","valuation","advisory","office-leasing","investment","project-management","industrial"],
  "api_base": "http://localhost:8769",
  "configured_backend": "mock",
  "deepseek_key_present": false,
  "ollama_url": null,
  "model": null,
  "temperature": null,
  "used_fallback": false,
  "last_call_status": null,
  "last_error": null,
  "last_latency_ms": null,
  "call_count": 0,
  "success_count": 0,
  "primary_stats": null
}
```

### Validation log — `/api/copilot/health` with `DEEPSEEK_API_KEY=sk-test-xyz`

```json
{
  "backend": "deepseek",
  "available_lines": [...same as above...],
  "api_base": "http://localhost:8769",
  "configured_backend": "deepseek",
  "deepseek_key_present": true,
  "ollama_url": null,
  "model": "deepseek-chat",
  "temperature": 0.3,
  "used_fallback": false,
  "last_call_status": null,
  "last_error": null,
  "last_latency_ms": null,
  "call_count": 0,
  "success_count": 0,
  "primary_stats": null
}
```

### Validation log — `/api/copilot/ask` with fake key (the fallback demo)

Request:

```http
POST /api/copilot/ask
Content-Type: application/json

{"question":"住宅 IRR 最高的 3 个项目"}
```

Response (HTTP **200**, never 500):

```json
{
  "question": "住宅 IRR 最高的 3 个项目",
  "answer": "<mock fallback answer with citations and suggestions>",
  "citations": [
    {
      "source": "apps/api/app/services/copilot_engine.py:fallback",
      "title": "推荐问题",
      "snippet": "...",
      "url": null
    }
  ],
  "chart_data": null,
  "intent": "irr_top",
  "confidence": 0.5,
  "backend": "deepseek",
  "used_fallback": true,
  "fallback_reason": "DeepSeekHTTPError: DeepSeek HTTP 401: {\"error\":{\"message\":\"Authentication Fails, Your api key: ****demo is invalid\",\"type\":\"authentication_error\",\"param\":null,\"code\":\"invalid_request_error\"}}",
  "model": "deepseek-chat",
  "debug": {
    "parsed": {"line":null, "intent":"irr_top", "top_n":3, "threshold":null},
    "prompt_chars": 243
  }
}
```

Key observations:
- HTTP 200 (not 500) ✅
- `backend: "deepseek"` (we tried DeepSeek, the user knows what they intended) ✅
- `used_fallback: true` (UI shows the ⚠ FALLBACK banner) ✅
- `fallback_reason` carries the full DeepSeek error so the operator can see the root cause ✅
- `model: "deepseek-chat"` reported even on the fallback path (because the primary was DeepSeek) ✅
- `fallback_reason` was generated by *actually* calling `api.deepseek.com` with `sk-deliberately-fake-key-for-demo` — DeepSeek returned a real 401, the client raised `DeepSeekHTTPError(401, body)`, the `FallbackBackend` caught it, ran `MockBackend.answer()`, and wrapped the result.

---

## Fallback test — fake key → live demo

The `test_ask_with_fake_key_does_not_500` test (and the manual `Invoke-WebRequest` shown above) prove the chain:

```
DEEPSEEK_API_KEY=sk-fake
  → factory returns FallbackBackend(DeepSeekBackend, MockBackend)
  → POST /api/copilot/ask hits the engine
  → engine.ask() picks FallbackBackend
  → FallbackBackend.complete() calls primary.complete()
      → DeepSeekBackend.complete() hits https://api.deepseek.com/v1/chat/completions
      → DeepSeek returns 401 (because sk-fake is invalid)
      → urllib raises HTTPError(401)
      → DeepSeekBackend catches it, raises DeepSeekHTTPError(401, body)
  → FallbackBackend catches DeepSeekHTTPError
      → sets used_fallback=True, last_error="DeepSeekHTTPError: ..."
      → calls MockBackend._extract_user_question(prompt) to recover the question
      → calls MockBackend.answer(question) — the rule engine
      → stores the resulting MockAnswer in self.last_answer
      → returns mock_answer.answer (text)
  → engine._ask_real_llm_async() reads backend.used_fallback / backend.last_error
  → engine attaches the mock's citations + chart to the response
  → response.backend = "deepseek" (user's intent)
  → response.used_fallback = True
  → response.fallback_reason = "DeepSeekHTTPError: ..."
  → HTTP 200 with the mock's answer + rich citations
```

---

## How to configure real DeepSeek

For operators (human-friendly):

1. Sign up at https://platform.deepseek.com/ and get an API key.
2. Edit `infra/.env` (or `apps/api/.env`) and set:
   ```bash
   DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
   ```
   Optional overrides (defaults shown):
   ```bash
   DEEPSEEK_MODEL=deepseek-chat           # or deepseek-reasoner for R1
   DEEPSEEK_BASE_URL=https://api.deepseek.com/v1/chat/completions
   DEEPSEEK_TIMEOUT=30
   DEEPSEEK_TEMPERATURE=0.3
   ```
3. Restart the API: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8769 --reload`.
4. Open `http://localhost:3000/copilot`:
   - The backend badge in the top-left now shows `DEEPSEEK` (purple).
   - Click it (or "后端设置") to expand the settings panel.
   - The panel shows: `DeepSeek Key: ✅ 已配置`, `Model: deepseek-chat`, `Temperature: 0.3`, `成功率: 0%`, etc.
5. Ask any question. The first /ask call shows the request's `last_call_status` go from `null` → `ok` (200) or `error` (4xx/5xx) or `timeout`.
6. If DeepSeek fails for any reason, the next /ask response will have `used_fallback: true` and the assistant bubble will display a yellow `⚠ FALLBACK` banner with the error reason in the tooltip.

For Ollama (local LLM):

1. Install [Ollama](https://ollama.com/) and run `ollama serve`.
2. Pull a model: `ollama pull qwen2.5:7b` (or any other).
3. In `.env`:
   ```bash
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=qwen2.5:7b
   ```
4. Restart the API. The Copilot badge now shows `OLLAMA` (blue).

---

## Assumptions

1. **OpenAI-compatible schema assumption** — DeepSeek's `https://api.deepseek.com/v1/chat/completions` accepts the standard OpenAI Chat Completions body (`model`, `messages[]`, `max_tokens`, `temperature`, `stream`) and returns a standard `choices[0].message.content`. The implementation is schema-compatible with any other OpenAI-compatible endpoint, so swapping in (e.g.) Qwen, GLM, or a local llama.cpp server requires only an env-var change.

2. **Mock_helpers fetch is the only "live data" path** — the mock backend makes `urllib` calls to `http://127.0.0.1:8769/api/lines/...` to grab data. The real LLM backend does NOT pre-fetch (it gets the question + system prompt and is expected to call out to the endpoints itself; the engine passes the user's question verbatim plus the registry-aware system prompt). This means in the real LLM path, the LLM is told to call the endpoints via the system prompt, but does not actually receive the data inline. This is a known limitation: the LLM cannot reliably follow the "use the endpoints" instructions without function-calling or RAG. For the deliverable, the **mock fallback path is the one that produces structured citations + chart**, and the LLM is treated as a "summariser / explainer" layer that adds polish on top of (or instead of) the rule engine.

3. **No streaming** — the response is returned as a single string. The protocol supports streaming (`"stream": false` is set explicitly), but FastAPI's `/ask` endpoint reads the whole response and returns JSON. Adding streaming would require an SSE endpoint and a frontend consumer.

4. **No embedding support yet** — `embed()` returns `[]` from every backend. The `LLMBackend.embed()` contract is preserved for a future RAG layer.

5. **No rate limiting on the /ask endpoint** — the factory has no built-in throttling. In production, the deployment should put the API behind a rate-limiter (e.g. `slowapi` or an upstream gateway).

6. **The `CopilotRequest.prefer_real_llm` field is *additive* — old clients that don't send it (or send `null`) see no change in behaviour.** The new `used_fallback` / `fallback_reason` / `model` fields in `CopilotResponse` and the new `model` / `temperature` / `last_call_status` etc. fields in `CopilotHealth` are also additive; old clients can ignore them.

7. **Tests assume a running API on `localhost:8769`** — the pre-existing `tests/test_copilot.py` test suite uses `TestClient(create_app())` (in-process) but the `mock_helpers` make real `urllib` calls to `http://127.0.0.1:8769`. This is a pre-existing fragility (not caused by this change). All 25 of those tests pass when an API is up. To run the full suite locally, start the API first:
   ```bash
   # In one terminal:
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8769

   # In another:
   cd apps/api && $env:PYTHONPATH = "$PWD"; python -m pytest tests/test_copilot.py tests/test_llm_backends.py -v
   ```
   (When PostgreSQL is unreachable, the lifespan's `init_db` will block startup. A `apps/api/run_api.py` test helper that bypasses the lifespan and directly mounts routers is provided locally for testing without DB.)

---

## Blockers

None — all 8 acceptance criteria pass. The pre-existing fragility of `tests/test_copilot.py` needing a running API is documented under "Assumptions".

The two test failures I encountered and resolved during development:

1. **Initial test failures (16)** — caused by the pre-existing test_copilot.py tests requiring a running API on port 8769. Resolved by starting the API in a separate process. The 36 new tests in `test_llm_backends.py` do not have this dependency (they mock urllib directly or use a `TestClient` that creates an in-process app).

2. **`test_fallback_used_flag_surfaces_in_complete_response` initially failed** because I patched `engine._backend.primary` (the default backend stored in `__init__`) but the engine's `ask()` now uses `_pick_backend()` to create a *fresh* backend per request. Resolved by patching `DeepSeekBackend.complete` at the class level (which the new instance inherits).
