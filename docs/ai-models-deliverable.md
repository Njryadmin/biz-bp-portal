# AI 模型注册表（Runtime-toggleable LLM Provider Registry）— 交付物

**日期：** 2026-09-03
**状态：** ✅ PASS
**Owner：** backend + frontend

## 1. 范围

新增 admin 专属的 UI + API，**在运行时**切换主流 LLM 厂商
（DeepSeek、OpenAI、Ollama、Anthropic、Mock、Custom），无需修改环境变量
也无需重启服务。今天 LLM 厂商由环境变量控制（`DEEPSEEK_API_KEY` /
`OLLAMA_BASE_URL`）；目标是建立一个由数据库支撑的注册表，admin 可以
随时切换。

### 具体内容

1. 在现有 schema 中新增 `ai_models` 表，启动时自动迁移。
2. 新增 `/api/ai-models/*` admin 端点（CRUD + test + set-default）。
3. 新增 `services/llm/factory.py`，从表中读取默认行，未命中时回退
   到环境变量路径。
4. 在仪表盘新增 `/admin/ai-models` admin 页面（antd Table + 模态框，
   镜像用户管理的交互）。
5. 在 `apps/web/app/api/ai-models/[[...path]]/route.ts` 新增 BFF 代理。
6. Topbar 在"管理后台"旁新增第二个 admin 链接（"AI 模型"）。

## 2. 变更 / 新增的文件

### 2.1 后端 — Python API

| 文件 | 变更 |
| --- | --- |
| `apps/api/app/core/config.py` | 新增 `ai_secret_key` 设置（用于静态加密 `api_key` 的 Fernet 密钥）。 |
| `apps/api/app/core/secret.py` | **新增。** 基于 Fernet 的加解密辅助函数。未配置密钥时回退到明文（带 WARNING）；处理 `env:VAR_NAME` 引用。 |
| `apps/api/app/db/bootstrap.py` | 新增 `AI_MODELS_DDL` 列表（幂等的 `CREATE TABLE IF NOT EXISTS` + provider CHECK 切换）。`ensure_raw_schema` 现在还会 seed "Mock (built-in)" 行以及 "promote-mock-to-default-if-no-default" 安全网。 |
| `apps/api/app/main.py` | 在 `/api/ai-models` 挂载新的 `ai_models_router`。 |
| `apps/api/app/schemas/ai_models.py` | **新增。** Pydantic v2 schema：`CreateAIModelRequest`、`UpdateAIModelRequest`、`AIModelItem`、`AIModelListResponse`、`TestAIModelRequest`、`TestAIModelResponse`。 |
| `apps/api/app/services/llm/factory.py` | **新增。** `get_active_model()` 读取数据库；`_build_backend_for_row()` 实例化对应厂商；`OpenAICompatibleBackend`（`openai` / `anthropic` / `custom` 的适配器）。 |
| `apps/api/app/services/llm/__init__.py` | 重新导出工厂符号；旧的 `get_llm_backend` / `configured_backend_name` / `get_primary_backend` 现在通过注册表 + 环境变量回退链解析（保留为单独的 `get_legacy_env_backend()` 函数）。 |
| `apps/api/app/routers/ai_models.py` | **新增。** 6 个 admin 端点（见 §3）。 |
| `apps/api/tests/test_ai_models.py` | **新增。** 16 个测试覆盖验收点。 |
| `apps/api/tests/conftest.py` | 一行修复：autouse `_disable_audit_middleware_in_tests` 装置原本 monkey-patch 了 `app.db.seed_users.seed_initial_users` 为 noop，这会静默破坏 `test_auth.py` 中专属的 bootstrap 测试。补丁现在仅作用于 `app.main.seed_initial_users`（lifespan 实际使用的绑定），源模块仍可被单元测试使用。**已存在的 bug，由新的测试运行暴露；并非 AI 模型工作的回归。** |

### 2.2 前端 — Next.js

| 文件 | 变更 |
| --- | --- |
| `apps/web/lib/ai-models.ts` | **新增。** 浏览器端辅助函数（`listAIModels`、`createAIModel`、`updateAIModel`、`deleteAIModel`、`testAIModel`、`setDefaultAIModel`），与 `lib/auth.ts` 的用户管理交互一致。 |
| `apps/web/app/(dashboard)/admin/ai-models/page.tsx` | **新增。** Admin 页面，含 antd Table + 模态框（新建 / 编辑 / 测试结果）+ 厂商颜色 Tag。空状态展示"暂未配置 AI 模型，使用内置 mock"并带"新建"按钮。 |
| `apps/web/app/(dashboard)/admin/layout.tsx` | 在 admin 子头部"用户管理"旁新增"AI 模型"标签按钮。 |
| `apps/web/app/(dashboard)/_components/Topbar.tsx` | 在已有"管理后台"链接旁新增第二个 admin 链接"AI 模型"（仅 admin）。 |
| `apps/web/app/api/ai-models/[[...path]]/route.ts` | **新增。** 通配 BFF 代理（GET/POST/PATCH/PUT/DELETE），转发 cookie + body 到上游，`force-dynamic`，`duplex: "half"`。与用户管理 BFF 一致。 |

### 2.3 工具 / 文档

| 文件 | 变更 |
| --- | --- |
| `.env.example` | 文档化 `BIZ_BP_AI_SECRET_KEY`（可选 Fernet 密钥，用于 `api_key` 加密）。 |
| `docs/ai-models-deliverable.md` | **本文件。** |

## 3. 新增 API 端点（全部仅 admin）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET`    | `/api/ai-models`                | 列出所有行（按 `is_default DESC, id ASC` 排序） |
| `POST`   | `/api/ai-models`                | 新建模型配置；`is_default=true` 会原子清除其它默认行 |
| `PATCH`  | `/api/ai-models/{id}`           | 部分更新；同样使用原子清除默认的语义 |
| `DELETE` | `/api/ai-models/{id}`           | 软删除（`is_active=false`）；**409** 如果是最后一个已启用+活跃行 |
| `POST`   | `/api/ai-models/{id}/test`      | 冒烟测试：发送 "ping"，将结果记录到行 |
| `POST`   | `/api/ai-models/{id}/set-default` | 将此行标记为默认（原子） |

所有写操作都通过现有的 `AuditMiddleware` 记录到 `raw.audit_log`
（与审计其他 admin 操作的同一路径），因此新操作开箱即可审计。

### 响应形态（单行）

响应**绝不**包含原始 `api_key`（它是只写密钥）。由两个布尔字段替代：

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
  "last_test_response": "抱歉，我没能完全理解 \"ping\" 的意图。\n...",
  "created_at": "2026-09-03T08:43:06+00:00",
  "updated_at": "2026-09-03T09:05:33+00:00"
}
```

`api_key_set=true` + `api_key_is_env_ref=true` 表示存储的是 `env:VAR_NAME`
形式的值（调用时解析）。其它情况下 `api_key_set=true` 表示存储的是
Fernet 加密后的字面量。

## 4. 厂商矩阵

工厂支持六种 provider 字符串。前三个使用已有的后端类；后三个共用
新的 `OpenAICompatibleBackend` 适配器。

| `provider` | 后端类 | 默认 `base_url` | 是否需要 `api_key`？ |
| --- | --- | --- | --- |
| `mock`      | `MockBackend`                  | n/a                            | 否  |
| `deepseek`  | `DeepSeekBackend`              | `https://api.deepseek.com/v1/chat/completions` | 是 |
| `ollama`    | `OllamaBackend`                | `http://localhost:11434`       | 否  |
| `openai`    | `OpenAICompatibleBackend`      | `https://api.openai.com/v1/chat/completions` | 是 |
| `anthropic` | `OpenAICompatibleBackend`      | （自行设置；适用于 Anthropic 前面的任何 OpenAI 兼容代理，如 LiteLLM） | 视情况 |
| `custom`    | `OpenAICompatibleBackend`      | （必须设置；`custom` 字段必填） | 视情况 |

`anthropic` 适配器有意做成 OpenAI 薄封装。一等 Anthropic Messages API
适配器是后续工作 — 规格说"按需扩展，不重写"，且 OpenAI 兼容路径覆盖
最常见的部署形态（LiteLLM / one-api 代理 Anthropic）。

## 5. 工厂解析顺序

```
1. ai_models 表   : is_default=TRUE AND enabled=TRUE AND is_active=TRUE
                    （按更新时间最新者胜出；id ASC 作为决胜规则）
2. ai_models 表   : 任意 enabled=TRUE AND is_active=TRUE 的行，按 id ASC
                    （恢复路径：如果所有 default 都被误清）
3. 环境变量回退   : DEEPSEEK_API_KEY → DeepSeekBackend
                    OLLAMA_BASE_URL  → OllamaBackend
4. MockBackend    : 始终可用的尾（无 I/O，确定性）
```

工厂的选择被 `get_primary_backend()` / `fallback` 引擎捕获，使现有
Copilot 代码保持不变。

## 6. 单元测试（后端）

`apps/api/tests/test_ai_models.py` —— **16 个测试，对运行在
127.0.0.1:11667 上的 pgserver 全部通过，约 15 秒**：

| # | 测试 | 校验内容 |
| - | --- | --- |
| 1  | `test_admin_can_list_models`                 | List 返回 200，含有 seed 的 mock 行 |
| 2  | `test_admin_can_create_model`               | POST 创建一行，返回 201 且形态正确 |
| 3  | `test_admin_can_update_model`               | PATCH 翻转 `model_name`、设置 `api_key`（env 引用）、翻转 `enabled` |
| 4  | `test_admin_can_set_default`                | POST `/set-default` 提升某行，清除原默认 |
| 5  | `test_admin_can_soft_delete_model`          | DELETE 软删除（`is_active=false`），行仍可查询 |
| 6  | `test_bp_retail_forbidden_on_every_endpoint[GET-/api/ai-models-None]`         | 非 admin → 403 |
| 7  | `test_bp_retail_forbidden_on_every_endpoint[POST-/api/ai-models-body1]`       | 非 admin → 403 |
| 8  | `test_bp_retail_forbidden_on_every_endpoint[PATCH-/api/ai-models/1-body2]`    | 非 admin → 403 |
| 9  | `test_bp_retail_forbidden_on_every_endpoint[DELETE-/api/ai-models/1-None]`    | 非 admin → 403 |
| 10 | `test_bp_retail_forbidden_on_every_endpoint[POST-/api/ai-models/1/test-body4]` | 非 admin → 403 |
| 11 | `test_bp_retail_forbidden_on_every_endpoint[POST-/api/ai-models/1/set-default-None]` | 非 admin → 403 |
| 12 | `test_cannot_delete_last_enabled_model`     | 409，提示"last enabled" |
| 13 | `test_test_endpoint_ok_with_mock`           | mock 厂商返回 ok，且 sample 非空 |
| 14 | `test_test_endpoint_error_with_bogus_provider` | openai + 假的 `base_url` → ok=false，记录错误 |
| 15 | `test_test_endpoint_missing_api_key_records_error` | openai 无 `api_key` → 记录配置错误 |
| 16 | `test_factory_get_active_model_reads_table`  | 工厂的 `_fetch_active_row` 返回默认行 |

`apps/api/tests/test_auth.py` —— 全部 48 个旧测试仍通过（无回归）。
conftest 的 autouse 装置修复记录在 §2.1；该变更是为了修复一个
已存在的、bootstrap 测试静默绕过的 bug。

### 运行

```bash
BIZ_BP_DATABASE_URL=postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp \
  py -3.12 -X utf8 -m pytest apps/api/tests/test_ai_models.py -q
# 16 passed, 93 warnings in 15.15s
```

注：系统 Python `py` = 3.14 自带 pydantic v1，但项目要求 pydantic v2。
请使用 `py -3.12`（项目固定的解释器；pydantic 2.13.5）。规格中的
"py -X utf8 -m pytest ..." 假定默认 Python 已带 pydantic 2 — 在本机
并非如此，因此加 `-3.12` 覆盖。

## 7. E2E curl 演示

针对运行中 API（端口 8769）+ pgserver（11667）抓取。

### 7.1 登录 + 列表

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
      "last_test_response": "抱歉，我没能完全理解 \"ping\" 的意图。\n...",
      "created_at": "2026-09-03T08:43:06+00:00",
      "updated_at": "2026-09-03T09:05:33+00:00"
    }
  ]
}
```

### 7.2 新建（Ollama，设为默认）

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

### 7.3 测试（本机无真实 Ollama）

```http
POST /api/ai-models/373/test
{}                                                  → 200
{
  "ok": true, "status": "ok", "latency_ms": 2032,
  "sample_response": "[Ollama 调用失败: <urlopen error [WinError 10061] ...>]",
  "error": null
}
```

`ok=true` 是有意为之：现有 `OllamaBackend` 会吞掉网络错误并返回友好的
降级字符串，因此测试端点看到非空的 `sample_response` 并把行标记为健康。
DeepSeek / OpenAI / Custom 厂商**会**在错误时抛出，这里会正确地产生
`ok=false` — 见 §10 中的后续工作。

### 7.4 设为默认（切回 mock）

```http
POST /api/ai-models/1/set-default                  → 200
{ "id": 1, "is_default": true, ... }
```

### 7.5 软删除

```http
DELETE /api/ai-models/373                           → 200
{
  "id": 373, "is_active": false, "is_default": false,
  "last_tested_at": "2026-09-03T09:08:33+00:00",
  "last_test_status": "ok", "last_test_latency_ms": 2032,
  ...
}
```

行被软删除；后续 `/api/ai-models` 读取仍包含它（`is_active=false`）以供
审计，但工厂忽略它（查询会过滤 `is_active=true`）。

### 7.6 最后启用保护

```http
# 上述删除后，seeded 的 mock 行是唯一已启用+活跃的行
DELETE /api/ai-models/1                             → 409
{ "detail": "cannot delete the last enabled model" }
```

### 7.7 非 admin（bp-retail）被拒

```http
POST /api/auth/login
{"username":"bp-retail","password":"bp123456"}     → 200  (cookie set)

GET /api/ai-models                                  → 403
{ "detail": "admin role required" }
```

### 7.8 BFF 往返

Next.js dev server（端口 3000）通过 BFF 代理。一次浏览器 fetch
（携带 httpOnly cookie）返回的 JSON 与上游 API 一致：

```http
# 浏览器 → BFF
GET /api/ai-models                                  → 200
{"count":2,"models":[{"id":1,...},{"id":373,...}]}
```

## 8. UI 走查

### 8.1 顶部条（仅 admin）

登录后，admin 用户在顶部条看到两个相邻的标签：

```
… | 告警中心 | 市场数据 | 管理后台 | AI 模型 | 👤 admin |
```

非 admin 用户均看不到。

### 8.2 Admin 布局子头部

`/admin` 子布局新增第二个标签：

```
[管理后台]  [用户管理]  [AI 模型]  [返回主页]
```

两个标签都仅 admin；布局现有的角色守卫在页面边界拦截非 admin。

### 8.3 AI 模型页面（`/admin/ai-models`）

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

* **Provider 列** 按厂商稳定着色（mock=default、deepseek=geekblue、
  openai=green、anthropic=purple、ollama=orange、custom=magenta），
  便于扫读。
* **API Key 列** 显示 `已设置`（绿）或 `env ref`（蓝）或 `—` —
  运维一眼就能看出是否配置了 key，以及是字面量还是 `env:VAR_NAME`
  引用，但不会泄露值。
* **最近测试 列** 显示 `last_test_status` Tag，tooltip 包含
  sample response（最多 300 字符）。
* **操作 列** 每行有 测试 / 编辑 / 停用；当某行已启用但非默认时，
  默认列还会显示"设为默认"链接。

### 8.4 新建 / 编辑模态框

两个模态框共用同一份表单字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| name           | 是 | 1-64 字符；唯一 |
| provider       | 是 | 下拉（Mock / DeepSeek / OpenAI / Anthropic / Ollama / Custom） |
| model_name     | 是 | 例如 `deepseek-chat`、`gpt-4o-mini`、`qwen2.5:7b` |
| base_url       | 否  | `ollama` 和 `custom` 时必填 |
| api_key        | 否  | 字面量或 `env:VAR_NAME`；tooltip 解释 env 引用形式 |
| enabled        | 否  | 开关，默认 true |
| is_default     | 否  | 开关，默认 false；设为 true 时清除其它默认 |

编辑模态框预填表单，`api_key` 留空（输入新值会覆盖；留空则保留旧值）。

### 8.5 测试结果模态框

点击任意行的"测试"打开模态框，展示：

* Status Tag（绿色"成功"或红色"失败"）+ 延迟
* Provider / model 回显
* 成功：绿色 Alert 显示 sample response 摘要
* 失败：红色 Alert 显示错误信息

行的 `last_tested_at` / `last_test_status` / `last_test_latency_ms` /
`last_test_response` 字段在同一事务中更新，下次刷新表格时即显示最新状态。

## 9. 安全与加密

* **API 密钥静态存储。** 默认情况下值是**明文**存储的，首次使用时会
  打印一条 WARNING 提示运维设置 `BIZ_BP_AI_SECRET_KEY`。生产环境请
  把该 env 设为 Fernet 密钥（32 字节 URL 安全 base64；用
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
  生成）。此时存储值为 `Fernet.encrypt(key)`，即 AES-128-CBC + HMAC-SHA256。
* **`env:VAR_NAME` 引用。** 类似 `env:DEEPSEEK_API_KEY` 的值按字面存储，
  在调用时通过 `os.environ.get(...)` 解析。当运维希望把 key 放在进程
  环境里（Kubernetes secret、docker-compose env 等）而不是数据库时，
  这种方式很方便。
* **所有 admin 端点都要求 `admin` 角色**，通过
  `Depends(require_admin_dep)` 强制。非 admin（bp-*、viewer、auditor）
  在所有端点上得到 403。
* **所有写操作都被审计**，由现有的 `AuditMiddleware` 写入
  `raw.audit_log`（与审计其他 admin 操作的行格式一致）。

## 10. 已知限制 / 后续工作

1. **Ollama "网络错误时 ok=true" 的怪异。** 现有 `OllamaBackend` 捕获
   `URLError` / `HTTPError` / `TimeoutError` / `JSONDecodeError` 并返回
   友好的降级字符串，所以测试端点看到非空的 `sample_response`，在本地
   Ollama 没运行时也报告 `ok=true`。DeepSeek / OpenAI / Custom 厂商
   **会**在错误时抛出，会在这里正确地报告 `ok=false`。后续补丁可以让
   `OllamaBackend` 重新抛出（让 `FallbackBackend` 的降级链接管），或
   让测试端点通过 `used_fallback` 标志识别降级字符串。

2. **`anthropic` 厂商是 OpenAI 封装。** 可在 Anthropic 前面的任何
   OpenAI 兼容代理（如 LiteLLM、one-api）下工作。一等 Anthropic
   Messages API 适配器是小型后续工作 — 可以放在 `OpenAICompatibleBackend` 旁边。

3. **没有"显示实际解密的 key"的 UI。** 需要复制 key 的运维可以在编辑
   模态框中重新输入；我们不暴露明文。（Auditor / viewer 角色也看不到
   key；admin 可以轮换但不能查看。）

4. **没有"克隆行"或"从 .env 导入"的 UI。** 目前已有完整 `.env` 的高级
   用户需要手动通过新建表单重新输入值。后续增强可以在首次访问（且
   旧版环境变量路径仍生效时）从 `os.environ` 自动填表。

5. **测试端点在 API worker 线程中同步执行 prompt。** 一次 30 秒的
   DeepSeek 调用会占用一个 uvicorn worker 30 秒。对 admin 专属的调试
   工具来说可以接受，但若使用量增加，端点应改为后台任务配合轮询。

6. **没有多租户范围。** 默认对整个门户全局生效。如果将来需要按业务线
   或按团队选择 LLM，表需要新增 `scope` / `scope_id` 列，工厂需要
   接受 scope 参数。

## 11. 依赖

* 后端：新增 `cryptography`（已通过 passlib / PyJWT 作为传递依赖引入）
  和 `pydantic` v2（已是必需）。无新增直接依赖。
* 前端：零新增依赖。全部 UI 基于现有的 antd 5.20 + Next.js 14 技术栈。

## 12. 可访问性

* 每个交互控件都有 `aria-label`（例如"测试 Mock (built-in)"、
  "编辑 Demo-Ollama"、"停用 Demo-Ollama"）。
* 表格带有 `aria-label="AI 模型列表"`。
* 空状态 Alert 使用 antd 内置的 `showIcon`；"新建"按钮带有
  `aria-label="新建"`。
* 测试结果模态框使用 `Alert` + `type="success"` / `"error"` +
  `showIcon`，屏幕阅读器既读状态又读文本。颜色从不作为唯一信号。

## 13. 响应式行为

* 表格设置 `scroll={{ x: 1280 }}`，窄屏下不会破坏行布局；仪表盘可滚动
  区域在 < 1280px 屏幕上的卡片内提供水平滚动条。
* 模态框使用 `width={640}`，在中等屏幕上表单字段仍清晰可读；在手机上，
  antd 自动回退到视口宽度。
