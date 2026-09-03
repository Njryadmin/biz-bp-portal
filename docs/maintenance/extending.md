# Extending — 扩展 Biz-BP Portal

> 读者：想新增业务线 / LLM / BFF 端点 / 告警规则 / 业务端点的工程师。
> 配套：[`MAINTENANCE.md`](../../MAINTENANCE.md) §7；[`docs/plugin-howto.md`](../plugin-howto.md)
> （更详细的 5 步流程 + YAML 字段说明）。

---

## 1. 新增一条业务线（最常见的扩展）

**承诺**：5 步复制-修改，0 行核心代码改动（已通过 `tests/test_p2_universality.py` 验证）。

### 1.1 5 步流程

| 步骤 | 操作 | 验证 |
|---|---|---|
| 1 | `cp -r business_lines\_template business_lines\<line_id>` | 目录已存在 |
| 2 | 编辑 `business_lines\<line_id>\manifest.yaml`（id / name / nav / api_prefix / warehouse） | `manifest.yaml` 的 `id` 字段 = 目录名 |
| 3 | 编辑 `business_lines\<line_id>\indicators.yaml`（8-10 个 KPI + 图表） | `BusinessLine.indicators` 通过 Pydantic 校验 |
| 4 | 把 `api\router.py.example` 重命名为 `api\router.py`，写 FastAPI router | `python -c "import importlib.util; m=importlib.util.spec_from_file_location('x', 'business_lines/<id>/api/router.py').loader; ..."` 不报错 |
| 5 | 在 `business_lines\registry.yaml` 加 1 行 | `GET /api/registry/lines` 数量从 9 → 10 |

**可选 6-9 步**（推荐但非必需）：
- 6. 写 `sensitivity.yaml` — 4 个输入 × N 个输出
- 7. 写 `forecast.yaml` — 时间序列定义
- 8. 写 `alerts.yaml` — 规则 + 阈值
- 9. 写 `data\seed\*.json` — 初始 mock 数据

### 1.2 详细参考

参见 [`docs/plugin-howto.md`](../plugin-howto.md) — 含 9 步完整 + 字段定义 + DBT 模型。

### 1.3 模板文件清单

`business_lines/_template/`：

```
_template/
├── manifest.yaml.example       ← 复制为 manifest.yaml，编辑
├── indicators.yaml.example     ← 复制为 indicators.yaml，编辑
├── api/router.py.example       ← 复制为 api/router.py，编辑
├── dbt/dbt_project.yml.example ← 复制为 dbt/dbt_project.yml
├── dbt/models/example.sql      ← dbt 模型样例
├── web/pages/_example.tsx      ← Next.js 页面样例
└── data/seed/.gitkeep          ← seed JSON 占位
```

**4 步模板用 `.example` 后缀是为了避免被自动发现**——重命名后才生效。

### 1.4 字段契约（manifest.yaml）

```yaml
id: <line_id>                  # URL-safe slug，必须等于目录名
name: "显示名"
version: 0.1.0
description: "业务描述"
owner: "bp@example.com"
icon: "HomeOutlined"           # @ant-design/icons 名称
nav:
  - path: "/<line_id>"         # RELATIVE 到 (dashboard) 根
    title: "概览"
  - path: "/<line_id>/trends"
    title: "趋势"
api_prefix: "/api/lines/<line_id>"  # 必须以 / 开头
warehouse:
  schema: "raw_<line_id>"
  dbt_schema: "stg_<line_id>"
  mart_schema: "mart_<line_id>"
refresh:
  schedule: "0 2 * * *"        # cron
  enabled: true
features:
  universal_kpi: true
  universal_chart: true
  ag_grid: true
```

校验在 `apps/api/app/core/registry.py:67-72`（Pydantic `field_validator`）。

### 1.5 注册后会发生什么

API 启动时（`apps/api/app/main.py:40`）`mount_business_line_routers(app)`：

1. 读 `business_lines/registry.yaml`
2. 对每个 entry，`importlib.util.spec_from_file_location` 加载
   `business_lines/<id>/api/router.py`
3. 找模块级的 `router` 或 `app`（APIRouter / FastAPI）
4. `app.include_router(router, prefix=<api_prefix>, dependencies=[...line-guard...])`

Web 启动时（`apps/web/app/(dashboard)/layout.tsx`）：

1. `GET /api/registry/lines`（带 cookie）
2. 渲染侧边栏（已用 `accessible_lines` 过滤）
3. `[line]/page.tsx` 与 `[line]/[page]/page.tsx` 走动态路由 + `linePageConfig.ts` 配置

### 1.6 自动种子

首次启动时（`apps/api/app/db/seed_users.py:141-231`）：
- 如果 `users` 表为空 → 创建 1 admin + 1 BP 用户（`bp-<line_id>`）**每个 line 1 个**
- `bp:<line_id>` 角色 + `user_business_lines` 行

**前提**：line 必须在 `registry.yaml` 里 + 名字跟目录一致。

### 1.7 验证

```powershell
# 重启 API
# 1. 业务线数 +1
curl -b cookies.txt http://127.0.0.1:8769/api/registry/lines | python -c "import json,sys; print(len(json.load(sys.stdin)['lines']))"
# 2. 新 line 路由可访问
curl -b cookies.txt http://127.0.0.1:8769/api/lines/<line_id>/ping
# 3. （如果有 sensitivity.yaml）4 个引擎都识别新 line
curl -b cookies.txt http://127.0.0.1:8769/api/sensitivity/profiles | python -c "import json,sys; print(len(json.load(sys.stdin)['profiles']))"
```

### 1.8 通用性测试

跑 `tests/test_p2_universality.py` 验证插件机制不被破坏：

```powershell
cd apps\api
python -m pytest tests\test_p2_universality.py -v
```

这个测试会**自动**在临时目录加 / 删 `test-line`（`registry.yaml` 不动），
验证：
- API 启动 0 报错
- 9 → 10 → 9 个业务线（自动回滚）
- 4 个引擎的 profile 数量同步
- 移除后**无残留**（关键）

### 1.9 失败模式

| 症状 | 原因 | 修法 |
|---|---|---|
| `ModuleNotFoundError: business_lines.<id>` | `api/router.py` 用了相对 import | 改成绝对 import（`from fastapi import APIRouter`） |
| API 启动时 500 `/api/lines/<id>/__error__` | 业务线 router 抛了 import 错 | 看 API log，定位具体行 |
| `manifest id 'X' does not match registry id 'Y'` | 目录名 vs manifest id 不一致 | 改其中之一 |
| `api_prefix must start with '/'` | manifest 的 `api_prefix` 漏了 / | 加 / |
| `ValueError: registry.yaml root must be a mapping` | YAML 写错（用 `[]` 而不是 `lines:`） | 加 `lines:` 顶层 key |

---

## 2. 新增一个 LLM 模型

**承诺**：管理后台一行 POST 即可，**不用**碰代码。

### 2.1 通过管理 UI

1. 登录 admin
2. 顶部菜单 → **AI 模型管理**
3. 点 **新建**
4. 填：
   - 名称（如 "DeepSeek-V3-Prod"）
   - 厂商（`openai` / `deepseek` / `ollama` / `mock` / `anthropic` / `custom`）
   - 模型名（如 `deepseek-chat` / `gpt-4o-mini` / `qwen2.5:7b`）
   - 基础 URL（`ollama` / `custom` 必填；其它可选）
   - API key（可填真实 key，或 `env:MY_KEY` 引用环境变量）
   - 启用 / 默认
5. 点 **测试** 验证连通
6. （可选）点 **设为默认** 让它成为新的活动模型

### 2.2 通过 API

```powershell
curl -b cookies.txt -X POST http://127.0.0.1:8769/api/ai-models `
  -H "Content-Type: application/json" `
  -d '{
    "name": "DeepSeek-V3-Test",
    "provider": "deepseek",
    "model_name": "deepseek-chat",
    "api_key": "sk-xxxxxxxx",
    "is_default": true
  }'
```

### 2.3 厂商矩阵

| 厂商 | base_url 默认 | 认证 | 备注 |
|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | Bearer | OpenAI 官方 |
| `deepseek` | `https://api.deepseek.com/v1` | Bearer | OpenAI 兼容 |
| `ollama` | `http://localhost:11434/v1` | 不需要（空 key） | 本地推理 |
| `mock` | — | — | 永远可用的规则引擎 |
| `anthropic` | （待实现） | — | 当前 factory 把它走 OpenAI 兼容路径，**不工作**——不要用 |
| `custom` | 用户填 | 取决于实现 | OpenAI 兼容端点 |

`anthropic` 的实现状态：factory 中 `_build_backend_for_row` 仅处理 openai / deepseek / ollama / mock / custom。
`anthropic` 会落到 fallback mock。**记录在 `apps/api/app/services/llm/factory.py` 的 TODO**。

### 2.4 env:VAR_NAME 引用

API key 字段支持两种格式：
- **字面值**：`"sk-..."` —— 数据库里存 Fernet 密文
- **环境变量引用**：`"env:DEEPSEEK_API_KEY"` —— 数据库里**只存引用**，运行期从 `os.environ` 读

后一种适合 CI / 生产，避免 secret 进数据库。

### 2.5 默认模型的选择

`apps/api/app/services/llm/factory.py`：
1. 查 `ai_models` 表 `is_default=TRUE AND is_active=TRUE` 的行
2. 用 `provider` 选 backend
3. 加密的 `api_key` 用 Fernet 解密（或解析 `env:` 引用）
4. **失败**（网络/认证）→ FallbackBackend → MockBackend

**永远有 mock 兜底**——即使没有 `is_default` 行（已被人类删了），
`apps/api/app/db/bootstrap.py:282-300` 会自动把第一个 `provider='mock' AND is_active=TRUE`
的行提升为 default。

### 2.6 切换效果

模型切换**不需要重启 API**——`factory.get_active_model` 每次调用都查表。

---

## 3. 新增一个 BFF 代理

**为什么需要 BFF**：浏览器 → Next.js (同源) → FastAPI (跨主机) 时，
现代浏览器不再带第三方 cookie。BFF 把浏览器请求转给同源的 Next.js，
由 Next.js 转发 cookie 到 FastAPI。

### 3.1 标准模式（推荐：catch-all）

参考 `apps/web/app/api/ai-models/[[...path]]/route.ts:1`：

```typescript
// apps/web/app/api/<feature>/[[...path]]/route.ts

import { NextResponse } from "next/server";
export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";

type Ctx = { params: { path?: string[] } };

function buildUrl(path: string[] | undefined): string {
  const tail = (path ?? []).join("/");
  return `${BASE}/api/<feature>${tail ? `/${tail}` : ""}`;
}

async function readBody(request: Request): Promise<Uint8Array | null> {
  if (request.method === "GET" || request.method === "HEAD") return null;
  const buf = await request.arrayBuffer();
  return buf.byteLength > 0 ? new Uint8Array(buf) : null;
}

async function proxy(request: Request, ctx: Ctx): Promise<Response> {
  const url = buildUrl(ctx.params.path);
  const method = request.method.toUpperCase();
  try {
    const headers: Record<string, string> = {
      cookie: request.headers.get("cookie") ?? "",  // ← 关键
    };
    if (method !== "GET" && method !== "HEAD") {
      headers["content-type"] =
        request.headers.get("content-type") ?? "application/json";
    }
    const body = await readBody(request);
    const upstream = await fetch(url, {
      method,
      headers,
      body: body ?? undefined,
      cache: "no-store",
      duplex: "half",  // undici 必需
    } as RequestInit);
    const buf = await upstream.arrayBuffer();
    const respHeaders = new Headers();
    const ct = upstream.headers.get("content-type");
    if (ct) respHeaders.set("content-type", ct);
    return new Response(buf, {
      status: upstream.status,  // ← 透传状态码
      headers: respHeaders,
    });
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
```

### 3.2 关键约束

| 约束 | 原因 |
|---|---|
| `export const dynamic = "force-dynamic"` | Next.js 14 默认会静态化 API 路由；强制动态 |
| `cookie: request.headers.get("cookie") ?? ""` | RBAC 依赖 cookie；不转就 401 |
| `content-type` 透传 | POST / PATCH 需要让上游知道是 JSON / form-data |
| `duplex: "half"` | undici 要求带 body 时声明 |
| `cache: "no-store"` | BFF 必须实时反映上游 |
| `upstream.status` 透传 | 401 / 403 / 422 都要原样返回 |

### 3.3 特殊端点：`/api/auth/login`

`apps/web/app/api/auth/login/route.ts:1` 是个例外：

- **不**转发 cookie（用户是匿名的）
- **必须**复制上游的 `set-cookie` 头到响应（httpOnly token）

```typescript
const setCookie = upstream.headers.get("set-cookie");
if (setCookie) {
  headers.append("set-cookie", setCookie);
}
```

### 3.4 动态业务线端点

业务线端点数量是动态的——每加一个 line 就多 N 个端点。
BFF 用一个 catch-all 覆盖所有：

```typescript
// apps/web/app/api/lines/[[...path]]/route.ts:1
const url = `${BASE}/api/lines/${path}${search}`;
```

任何 `/api/lines/<line>/<sub>/...` 都会被这个 catch-all 捕获。

### 3.5 不要做的事

- 不要在 BFF 里做**任何**业务逻辑（不解析 JWT，不查 DB，不验角色）
- 不要**缓存**响应
- 不要在 BFF 里 `redirect()`——会让浏览器对上游响应不知所措
- 不要在 BFF 里调 `next/headers.cookies()`——BFF 看到的是浏览器发的，不是 Next 渲染的

---

## 4. 新增告警规则

**位置**：`business_lines/<line>/alerts.yaml`

### 4.1 YAML 格式

```yaml
line_id: residential
line_name: "住宅分析"

rules:
  - id: irr_below_threshold            # 唯一
    name: "动态 IRR 低于阈值"            # 显示名
    indicator_id: dynamic_irr          # 引用 indicators.yaml
    operator: "<"                      # < > >= <= == between change_pct consecutive
    threshold: 0.10
    severity: high                     # low / medium / high
    message_template: "{project} 动态 IRR {value:.2%}，低于阈值 10%"
    enabled: true
    channels: [in_app, email]
    scope: project                     # project / line

  - id: irr_between_band
    name: "IRR 警戒带"
    indicator_id: dynamic_irr
    operator: between
    threshold: [0.10, 0.15]            # between 必须 2 元素数组
    severity: low
    message_template: "{project} IRR {value:.2%} 处于 10%-15%"
    enabled: true
    channels: [in_app]
    scope: project
```

### 4.2 操作符矩阵

| 操作符 | threshold 形式 | 含义 |
|---|---|---|
| `>` `<` `>=` `<=` `==` | 数字 / 字符串 | 直接比较 |
| `between` | 2 元素数组 `[lo, hi]` | 在区间内 |
| `change_pct` | 数字（百分比） | 与上一期对比 |
| `consecutive` | 数字（N） | 连续 N 期满足 `<` / `>` |

实现：`apps/api/app/services/alert_engine.py`（~700 LOC，0 业务线硬编码）。

### 4.3 触发流程

1. 用户点 **告警** 页面 → 调 `POST /api/alerts/check`
2. 服务端读 `alerts.yaml` + 调 `/api/lines/<line>/indicators` 拉 KPI
3. 应用所有 rule，过滤 `enabled: true`
4. 返回触发的 alert（含 `severity` / `message` / `attribution` 建议）

### 4.4 attribution 字段

```yaml
attribution:
  - id: market
    name: 市场因素
    drivers: [竞品开盘, 政策变化, 利率变化]
```

用于"为什么这条 alert 触发"的根因分析。**可选**。

### 4.5 验证

```powershell
# 1. rule 出现在 profile 列表
curl -b cookies.txt http://127.0.0.1:8769/api/alerts/profiles
# 2. 手动触发检查
curl -b cookies.txt -X POST http://127.0.0.1:8769/api/alerts/check -H "Content-Type: application/json" -d '{"line_id": "<line>"}'
# 3. 查历史
curl -b cookies.txt http://127.0.0.1:8769/api/alerts/history?line_id=<line>&limit=10
```

---

## 5. 新增 API 端点

### 5.1 在已有 router 加端点

例：在 `apps/api/app/routers/alerts.py` 加一个新端点：

```python
from fastapi import APIRouter, Depends, HTTPException

from ..core.auth import CurrentUser
from ..core.rbac import require_admin_dep  # 或 require_auditor_or_admin_dep / business_line_dep

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

@router.get("/my-new-endpoint")
async def my_new_endpoint(
    user: CurrentUser = Depends(require_admin_dep),  # ← 守卫
) -> dict:
    """Brief description."""
    # 业务逻辑
    return {"ok": True}
```

### 5.2 角色守卫选择

| 场景 | 用 |
|---|---|
| **任何已认证用户** | `Depends(get_current_user)` |
| **仅 admin** | `Depends(require_admin_dep)` |
| **admin 或 auditor** | `Depends(require_auditor_or_admin_dep)` |
| **行级（path 含 line_id）** | `Depends(business_line_dep())` 或 `business_line_dep(require_write=True)` |
| **自定义角色** | `Depends(require_role("admin", "viewer"))` |

实现：`apps/api/app/core/rbac.py:1`。

### 5.3 写数据库的标准模式

```python
from sqlalchemy import text
from ..db.session import get_session_factory

@router.post("/upload-thing")
async def upload_thing(
    payload: SomeSchema,
    user: CurrentUser = Depends(require_admin_dep),
) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text("INSERT INTO raw.uploads (upload_id, ...) VALUES (:uid, ...)"),
            {"uid": str(uuid.uuid4()), ...},
        )
        await session.commit()
    return {"ok": True}
```

**约定**：
- 数据入 `raw` schema（landing / scrapers）用 `raw.uploads`（已建好）
- 业务数据入 `<schema>` 命名空间（DBT 维护）
- 每次 INSERT 必须带 `upload_id`（UUID-like 字符串，便于 DBT 追溯）

### 5.4 Pydantic schema

每个新端点的 body / response 都要在 `apps/api/app/schemas/` 加 Pydantic 模型。

```python
# apps/api/app/schemas/my_feature.py
from pydantic import BaseModel, Field, EmailStr
from typing import Optional

class MyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    email: Optional[EmailStr] = None  # ← 注意 EmailStr 拒空串（见 §6）
    clear_email: bool = False         # ← 清空标志

class MyResponse(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    created_at: str
```

### 5.5 同步加 TypeScript 类型

每加一个 Pydantic response，**必须**同步加到 `packages/types/src/index.ts`。
否则前端会用 `any` 接收，破坏类型安全。

---

## 6. Pydantic schema 约定

### 6.1 "可选且可清空"的字段

Pydantic 的 `EmailStr` 把 `""` 当非法。`Optional[str]` 不会。

```python
# 错：用户清空时前端发 email: ""，后端 422
email: Optional[EmailStr] = None

# 对：拆成"值"和"清空标志"两个字段
email: Optional[EmailStr] = None
clear_email: bool = False
```

实现：`apps/api/app/routers/auth.py` 的 PATCH users 端点。

### 6.2 显式"清空"模式（ai_models.api_key）

```python
# 错：空字符串和"未传"难区分
api_key: Optional[str] = None

# 对：约定空字符串 = "清空"，None = "未传"
api_key: Optional[str] = None

# 在 router 里：
if payload.api_key == "":
    # 清空
    await session.execute(text("UPDATE ai_models SET api_key = NULL WHERE id = :id"), {"id": id})
elif payload.api_key is not None:
    # 更新（加密后入库）
    encrypted = encrypt_secret(payload.api_key)
    await session.execute(text("UPDATE ai_models SET api_key = :k WHERE id = :id"), {"id": id, "k": encrypted})
```

参考：`apps/api/app/routers/ai_models.py` 的 PATCH 端点。

### 6.3 别名（YAML ↔ Pydantic）

Pydantic 字段名不能用 `schema`（保留），用 alias：

```python
class BusinessLineWarehouse(BaseModel):
    model_config = {"populate_by_name": True}
    schema_name: str = Field(alias="schema")
    dbt_schema: str
    mart_schema: str
```

参考：`apps/api/app/core/registry.py:32-39`。

### 6.4 校验器

```python
@field_validator("api_prefix")
@classmethod
def _api_prefix_must_start_with_slash(cls, v: str) -> str:
    if not v.startswith("/"):
        raise ValueError(f"api_prefix must start with '/', got: {v}")
    return v
```

放 class 内，class method。**优先** 用 Pydantic 校验而不是手写 if/else。

### 6.5 Enum 模式

```python
# 1. Literal（最简单）
ProviderName = Literal["openai", "deepseek", "ollama", "mock", "anthropic", "custom"]

# 2. Enum（需要遍历 / 反射时）
from enum import Enum
class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
```

DB 层的 CHECK 约束与 Pydantic Literal 必须**双写**——加新枚举值要改两处。
参考：`apps/api/app/schemas/ai_models.py:24-33` + `apps/api/app/db/bootstrap.py:202-212`。

---

## 7. 跨业务线扩展的验证

任何"动了核心代码"的扩展，跑这两个测试：

```powershell
cd apps\api
python -m pytest tests\test_p2_universality.py -v
python -m pytest tests\test_registry.py -v
```

第一个验证"加 / 删业务线 0 核心代码改动"；第二个验证 YAML schema 校验。
