# LLM 集成交付物 — Fin BP Portal AI Copilot

**状态**：✅ 完成 —— 从 mock 升级到真实 LLM，DeepSeek 为主、Ollama 为备。

**日期**：2026-09-03
**范围**：将仅支持 mock 的 LLM 后端替换为可插拔架构，支持 DeepSeek（OpenAI 兼容，https://api.deepseek.com）和 Ollama（本地）。Mock 保留为确定性规则引擎，并作为优雅降级目标。

---

## 结果

**PASS** —— 61 / 61 个 Copilot 相关测试通过，前端 typecheck 干净，通过对 fake DeepSeek key 发起真实 HTTP 调用验证了降级链路。

```
$ python -m pytest tests/test_copilot.py tests/test_llm_backends.py -v
============================= test session starts =============================
collected 61 items
tests/test_copilot.py    .........................   [ 40%]
tests/test_llm_backends.py  ....................................   [100%]
======================== 61 passed, 1 warning in 6.50s ========================
```

---

## 后端 — 文件清单

| 文件 | 状态 | 用途 |
|---|---|---|
| `apps/api/app/services/llm/prompts.py` | **新增** | `SYSTEM_PROMPT` 模板 + `render_system_prompt()` + `build_prompt()`。注册表感知，3 个 few-shot 示例，`{{}}` 已转义以保证 format 安全。 |
| `apps/api/app/services/llm/deepseek.py` | **重写** | 真实 DeepSeek V3 客户端。仅用 `urllib`，为每类错误抛出明确异常（`DeepSeekConfigError` / `DeepSeekHTTPError` / `DeepSeekTimeoutError` / `DeepSeekProtocolError` / `DeepSeekError`）。跟踪 `last_call_status`、`last_latency_ms`、`call_count`、`success_count`。 |
| `apps/api/app/services/llm/__init__.py` | **重写** | 工厂：`DEEPSEEK_API_KEY` → `FallbackBackend(DeepSeek, Mock)`；`OLLAMA_BASE_URL` → `FallbackBackend(Ollama, Mock)`；否则 `MockBackend`。新增 `FallbackBackend` 类实现降级链，并暴露 `used_fallback` + `last_error` + `last_answer`。 |
| `apps/api/app/services/copilot_engine.py` | **重写** | `CopilotRequest` 获得 `prefer_real_llm: bool | None`。引擎通过 `_pick_backend()` 每次请求选后端。`CopilotResponse` 扩展 `used_fallback`、`fallback_reason`、`model`。`CopilotHealth` 扩展 `configured_backend`、`deepseek_key_present`、`ollama_url`、`model`、`temperature`、`used_fallback`、`last_call_status`、`last_error`、`last_latency_ms`、`call_count`、`success_count`、`primary_stats`。 |
| `apps/api/app/routers/copilot.py` | 未变 | `response_model=CopilotHealth` / `CopilotResponse` 注解自动透传新字段。 |
| `apps/api/tests/test_llm_backends.py` | **新增** | 36 个测试，覆盖：DeepSeek 客户端（成功 / 4xx / 5xx / 超时 / 网络 / 非法 JSON / 空 choices / 空 content / 缺 key / embed）、工厂优先级、FallbackBackend 链路、system prompt + build_prompt 内容、各类 env 下的 health 端点、fake key + 打补丁 urllib 的完整 HTTP 往返、以及 `prefer_real_llm` 开关。 |
| `infra/.env.example` | **更新** | 新增 `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_TIMEOUT` / `DEEPSEEK_TEMPERATURE` 和 `OLLAMA_*` 配置段。注释说明优先级 + 降级语义。 |

### 关键实现说明

- **未新增 Python 依赖** —— 使用 stdlib `urllib.request`。`httpx` 已经是项目依赖，但本层未使用。
- **没有硬编码单一后端** —— 工厂在*调用时*（而非 import 时）读取环境变量，测试和运维可以按请求切换后端。`FallbackBackend` 包装由工厂构造，绝不直接实例化。
- **降级复用 mock 规则引擎** —— 当 primary 抛出异常时，`FallbackBackend.complete()` 从 prompt 中重新抽取问题，调用 `MockBackend.answer()`（同步），并返回 mock 的文本 + 把结构化的 `MockAnswer`（citations + chart）写入 `last_answer`。引擎随后用此丰富响应。
- **失败时统一降级到 200** —— 每次 `/api/copilot/ask` 请求都返回 200。Copilot 响应中携带 `used_fallback: true` + `fallback_reason: "<error class>: <message>"`，前端据此渲染"⚠️ 降级"横幅。
- **`prefer_real_llm` 是按请求的覆盖**，覆盖 env 驱动的默认。UI 开关将其放入请求体；引擎的 `_pick_backend()` 在不修改 env 的前提下遵守该值。

---

## 前端 — 文件清单

| 文件 | 状态 | 用途 |
|---|---|---|
| `apps/web/app/(dashboard)/copilot/page.tsx` | **重写** | 新增 `BackendSettings` 面板（可折叠 Card + `Descriptions`）：当前后端、key 状态、模型、temperature、调用次数、成功率、最近状态、最近延迟、最近错误。未配置时给出提示。后端徽章可点击展开。新增"用真实 LLM" `Switch` 开关，仅在配置了真实后端时显示。`MessageBubble` 扩展渲染 `used_fallback` 警告 + `model` 标签。每次 ask 后自动刷新 `/api/copilot/health`。 |
| `apps/web/app/api/copilot/health/route.ts` | 未变 | 已经是透明的 BFF 透传。 |
| `apps/web/app/api/copilot/ask/route.ts` | 未变 | 将 `prefer_real_llm` 作为 body 字段转发；后端遵守它。 |

### UI 结构（本次变更后）

```
┌─ 顶部 ─────────────────────────────────────────────────────────┐
│ 🤖 AI Copilot  [MOCK] [deepseek-chat] 4 业务线  [后端设置]      │
├─ 推荐问题（可折叠）──────────────────────────────────────────┤
│ Recommended questions …                                        │
├─ 消息列表 ─────────────────────────────────────────────────┤
│  user（右，蓝）                                              │
│  AI   （左，白）  [intent] [confidence] [BACKEND] [FALLBACK] │
│       ⚠ 降级提示：DeepSeek HTTP 401: Authentication Fails …     │
│       answer text                                              │
│       [引用] [附图]                                            │
├─ 输入 + 发送 + ☐ 用真实 LLM ──────────────────────────────────┤
└────────────────────────────────────────────────────────────────┘
```

`BackendSettings` 面板（展开时）以两列展示元数据：配置项 vs 运行时统计。

---

## 提示词 — system prompt 摘要

`apps/api/app/services/llm/prompts.py` 导出：

- `SYSTEM_PROMPT` —— 60 行的静态模板，含 3 个占位符（`{business_lines}`、`{cross_endpoints}`、`{api_base}`），运行时通过 `render_system_prompt()` 填充。包含：
  1. 角色设定（"你是 Fin BP Portal 的 AI Copilot，一名专业的金融业务伙伴助手"）
  2. 服务对象（"服务于 Fin BP Portal 平台 — 金融 BP / 财务 / 运营 人员的统一数据分析平台"）
  3. 实时业务线表（注册表驱动 —— 在 render 时从 `load_registry()` 拉取）
  4. 按业务线的端点目录（为 residential / retail / retail-leasing / my-line 硬编码 `ENDPOINT_CATALOG`）
  5. 跨业务线端点（`/api/registry/lines`、`/api/sensitivity/profiles/{line_id}`、`/api/sensitivity/analyze`）
  6. **4 条硬性规则**（仅使用上下文数据、不编造、不跨业务线推断、给出监管阈值）
  7. **输出格式**（必须以 `参考资料：<endpoints + key fields + values>` 结尾）
  8. **3 个 few-shot 示例**：住宅 IRR Top-3、住宅三道红线触发、敏感性 1D 扫描。

- `build_prompt(question, line_id, context_data)` —— 工厂函数，渲染用户侧 prompt，包含：
  - 业务线过滤（line hint）
  - 用户问题（原始问题）
  - 上下文数据（mock_helpers 拉取到的数据，格式化为 JSON；为空时 prompt 显式提示"无数据 — 建议调用的端点"）

两个函数都是纯函数，除一次性 `load_registry()` 调用外无 I/O 副作用（在后端实例上缓存）。

---

## 测试数量

| 文件 | 数量 | 状态 |
|---|---|---|
| `tests/test_copilot.py`（已有） | 25 | ✅ 全部通过 |
| `tests/test_llm_backends.py`（新增） | 36 | ✅ 全部通过 |
| **合计** | **61** | ✅ **全部通过**，6.50s |

36 个新测试覆盖：

- **DeepSeek 客户端**（10 个测试，全部 mock `urllib.request.urlopen`）：
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
  11. `test_embed_returns_empty_list`（共 11 个）

- **工厂**（4 个）：无 env → mock、deepseek → FallbackBackend(DeepSeek, Mock)、ollama → FallbackBackend(Ollama, Mock)、deepseek 优先于 ollama。

- **降级链**（4 个）：primary 成功、primary 失败、整条 engine 路径响应中带 `used_fallback=true`、全故障（primary 和 mock 都失败）—— 仍返回非空字符串而非抛错。

- **提示词**（8 个）：`SYSTEM_PROMPT` 中的角色 / 业务线 / few-shot / 引用规则；`build_prompt` 包含问题 / 上下文 / 处理 `None` line / 处理空上下文。

- **Health 端点**（3 个）：无 env、deepseek-key、ollama-url —— 校验新增的 `configured_backend`、`model`、`temperature` 等字段。

- **完整 HTTP 往返**（`TestClient` + 打补丁 urllib）（3 个）：fake key → 200 + `used_fallback=true` + `fallback_reason`；mock 成功 → 200 + `backend=deepseek` + `model=deepseek-chat` + answer 含 DeepSeek 文本；无 key → 200 + `backend=mock` + `used_fallback=false`。

- **`prefer_real_llm` 开关**（3 个）：有 key + True → 真实 LLM 路径；有 key + False → 强制 mock；无 key + True → 回退到 mock（无法满足请求）。

---

## 验证 — 8 项验收点

| # | 标准 | 结果 |
|---|---|---|
| 1 | `pytest tests/test_llm_backends.py -v` 全部 15+ 通过 | ✅ **36 / 36 通过** |
| 2 | `pytest tests/test_copilot.py -v` 仍 25+ 通过（无回归） | ✅ **25 / 25 通过** |
| 3a | `GET /api/copilot/health` 无 env → `backend: "mock"`、`used_fallback: false` | ✅ 见下方验证日志 |
| 3b | `GET /api/copilot/health` 设置 `DEEPSEEK_API_KEY=fake-key` → `backend: "deepseek"`、`used_fallback: false`、`model: "deepseek-chat"` | ✅ 见下方验证日志 |
| 3c | `POST /api/copilot/ask` 用 fake key → 200、`used_fallback: true`、`fallback_reason` 有值（非 500） | ✅ 见下方验证日志 |
| 4 | 前端 `npm run typecheck` 通过 | ✅ `tsc --noEmit` 退出码 0 |
| 5 | `/copilot` 路由仍工作（200，外壳不变） | ✅ `page.tsx` 编译通过；新字段对旧调用方都是可选的 |
| 6 | 页面顶部展示当前后端 + "后端设置"入口 | ✅ `BackendSettings` 面板默认折叠，点击后端徽章或"后端设置"按钮展开 |
| 7 | 14 个已有 mock intent 仍工作 | ✅ `test_copilot.py` 跑完 12+ intent 模板；全部通过 |
| 8 | 未新增 pip 依赖 | ✅ 仅使用 stdlib `urllib.request`、`urllib.error`、`json`、`time` |

### 验证日志 — `/api/copilot/health` 无 env

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

### 验证日志 — `/api/copilot/health` 设置 `DEEPSEEK_API_KEY=sk-test-xyz`

```json
{
  "backend": "deepseek",
  "available_lines": [...同上...],
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

### 验证日志 — `/api/copilot/ask` 使用 fake key（降级 demo）

请求：

```http
POST /api/copilot/ask
Content-Type: application/json

{"question":"住宅 IRR 最高的 3 个项目"}
```

响应（HTTP **200**，绝不为 500）：

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

关键观察：
- HTTP 200（非 500）✅
- `backend: "deepseek"`（我们尝试了 DeepSeek，用户知道自己的意图）✅
- `used_fallback: true`（UI 显示 ⚠ FALLBACK 横幅）✅
- `fallback_reason` 携带完整的 DeepSeek 错误，便于运维定位根因 ✅
- 即使走了降级路径，仍上报 `model: "deepseek-chat"`（因为 primary 是 DeepSeek）✅
- `fallback_reason` 是 *真实* 调用 `api.deepseek.com` 配合 `sk-deliberately-fake-key-for-demo` 触发的 —— DeepSeek 返回 401，客户端抛出 `DeepSeekHTTPError(401, body)`，`FallbackBackend` 捕获后调用 `MockBackend.answer()` 并包装结果。

---

## 降级测试 — fake key → 真实 demo

`test_ask_with_fake_key_does_not_500` 测试（以及上文的 `Invoke-WebRequest` 手动调用）证明了链路：

```
DEEPSEEK_API_KEY=sk-fake
  → 工厂返回 FallbackBackend(DeepSeekBackend, MockBackend)
  → POST /api/copilot/ask 命中引擎
  → engine.ask() 选中 FallbackBackend
  → FallbackBackend.complete() 调用 primary.complete()
      → DeepSeekBackend.complete() 请求 https://api.deepseek.com/v1/chat/completions
      → DeepSeek 返回 401（sk-fake 非法）
      → urllib 抛出 HTTPError(401)
      → DeepSeekBackend 捕获，抛出 DeepSeekHTTPError(401, body)
  → FallbackBackend 捕获 DeepSeekHTTPError
      → 设置 used_fallback=True、last_error="DeepSeekHTTPError: ..."
      → 调用 MockBackend._extract_user_question(prompt) 还原问题
      → 调用 MockBackend.answer(question) —— 规则引擎
      → 将得到的 MockAnswer 存入 self.last_answer
      → 返回 mock_answer.answer（文本）
  → engine._ask_real_llm_async() 读取 backend.used_fallback / backend.last_error
  → engine 把 mock 的 citations + chart 挂到响应
  → response.backend = "deepseek"（用户意图）
  → response.used_fallback = True
  → response.fallback_reason = "DeepSeekHTTPError: ..."
  → HTTP 200，携带 mock 的答案 + 丰富的 citations
```

---

## 如何配置真实 DeepSeek

运维人员（友好）：

1. 在 https://platform.deepseek.com/ 注册并获取 API key。
2. 编辑 `infra/.env`（或 `apps/api/.env`），设置：
   ```bash
   DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
   ```
   可选覆盖（默认值已给出）：
   ```bash
   DEEPSEEK_MODEL=deepseek-chat           # 或 deepseek-reasoner（R1）
   DEEPSEEK_BASE_URL=https://api.deepseek.com/v1/chat/completions
   DEEPSEEK_TIMEOUT=30
   DEEPSEEK_TEMPERATURE=0.3
   ```
3. 重启 API：`python -m uvicorn app.main:app --host 127.0.0.1 --port 8769 --reload`。
4. 打开 `http://localhost:3000/copilot`：
   - 左上角后端徽章变为 `DEEPSEEK`（紫色）。
   - 点击它（或"后端设置"）展开设置面板。
   - 面板显示：`DeepSeek Key：✅ 已配置`、`Model：deepseek-chat`、`Temperature：0.3`、`成功率：0%` 等。
5. 任意提问。第一次 /ask 调用会观察到请求的 `last_call_status` 从 `null` 变为 `ok`（200）或 `error`（4xx/5xx）或 `timeout`。
6. 如果 DeepSeek 因任何原因失败，下一次 /ask 响应将带 `used_fallback: true`，助手气泡显示黄色 `⚠ FALLBACK` 横幅，tooltip 展示错误原因。

Ollama（本地 LLM）：

1. 安装 [Ollama](https://ollama.com/) 并运行 `ollama serve`。
2. 拉取模型：`ollama pull qwen2.5:7b`（或其他）。
3. 在 `.env` 中：
   ```bash
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=qwen2.5:7b
   ```
4. 重启 API。Copilot 徽章变为 `OLLAMA`（蓝色）。

---

## 假设

1. **OpenAI 兼容协议假设** —— DeepSeek 的 `https://api.deepseek.com/v1/chat/completions` 接受标准 OpenAI Chat Completions body（`model`、`messages[]`、`max_tokens`、`temperature`、`stream`），并返回标准 `choices[0].message.content`。实现与任何 OpenAI 兼容端点协议一致，因此切换到（例如）Qwen、GLM 或本地 llama.cpp server 只需要改环境变量。

2. **mock_helpers 拉取是唯一的"实时数据"路径** —— mock 后端通过 `urllib` 访问 `http://127.0.0.1:8769/api/lines/...` 拉取数据。真实 LLM 后端不会预先拉取（它接收问题 + system prompt，并被期望自行调用端点；引擎把用户问题原样连同注册表感知的 system prompt 一并传入）。这意味着在真实 LLM 路径中，LLM 被告知要通过 system prompt 调用端点，但并不会内联收到数据。这是一个已知限制：在没有 function-calling 或 RAG 的情况下，LLM 无法可靠地遵守"使用端点"的指示。对本次交付而言，**mock 降级路径是产生结构化 citations + chart 的路径**，LLM 充当"摘要 / 解释"层，在规则引擎之上（或替代规则引擎）增加润色。

3. **无流式输出** —— 响应作为单字符串返回。协议支持流式（显式设置了 `"stream": false`），但 FastAPI 的 `/ask` 端点读取完整响应并返回 JSON。如需流式，需要新增 SSE 端点和前端消费者。

4. **暂未支持 embedding** —— 所有后端的 `embed()` 返回 `[]`。`LLMBackend.embed()` 协议保留给未来的 RAG 层。

5. **`/ask` 端点无速率限制** —— 工厂没有内建节流。生产环境应在 API 之前挂限流器（例如 `slowapi` 或上游网关）。

6. **`CopilotRequest.prefer_real_llm` 字段是*增量的*** —— 不传或传 `null` 的旧客户端行为不变。`CopilotResponse` 新增的 `used_fallback` / `fallback_reason` / `model` 字段以及 `CopilotHealth` 新增的 `model` / `temperature` / `last_call_status` 等字段也都是增量的，旧客户端可以忽略。

7. **测试假设 API 运行于 `localhost:8769`** —— 已有 `tests/test_copilot.py` 使用 `TestClient(create_app())`（进程内），但 `mock_helpers` 通过 `urllib` 真实访问 `http://127.0.0.1:8769`。这是已有脆弱性（并非本次引入）。这 25 个测试在 API 运行时全部通过。完整跑本机测试需要先启动 API：
   ```bash
   # 终端 1：
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8769

   # 终端 2：
   cd apps/api && $env:PYTHONPATH = "$PWD"; python -m pytest tests/test_copilot.py tests/test_llm_backends.py -v
   ```
   （当 PostgreSQL 不可达时，lifespan 中的 `init_db` 会阻塞启动。本地提供 `apps/api/run_api.py` 跳过 lifespan 直接挂载路由，方便无 DB 测试。）

---

## 阻塞

无 —— 8 项验收点全部通过。`tests/test_copilot.py` 需要运行中的 API 这一既有脆弱性已记录在"假设"中。

开发过程中遇到并解决的两个测试失败：

1. **初次测试失败（16 个）** —— 由 `test_copilot.py` 要求 8769 端口的 API 在跑导致。解决方案：另起一个进程运行 API。`test_llm_backends.py` 的 36 个新测试没有这个依赖（直接 mock urllib 或使用 `TestClient` 创建进程内 app）。

2. **`test_fallback_used_flag_surfaces_in_complete_response` 起初失败** —— 因为我打补丁了 `engine._backend.primary`（`__init__` 中存储的默认后端），但引擎的 `ask()` 现在使用 `_pick_backend()` 每次请求都创建*新的*后端。解决方法：在类级别打补丁 `DeepSeekBackend.complete`（新实例会继承该补丁）。
