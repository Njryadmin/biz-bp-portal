# Conventions — 编码规范

> 读者：所有写本仓库代码的人（人 + AI）。
> 配套：[`MAINTENANCE.md`](../../MAINTENANCE.md) §9 约定；[`AGENTS.md`](../../AGENTS.md) §6 约定。

每条规范：**是什么** + **为什么** + **反例**。

---

## 1. Python 规范

### 1.1 导入顺序

```python
# 1. __future__ 导入（每个文件第一行 import 之前）
from __future__ import annotations

# 2. 标准库
import asyncio
import os
from pathlib import Path

# 3. 第三方
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

# 4. 本地（项目内）
from ..core.auth import CurrentUser
from ..core.rbac import require_admin_dep
from ..db.session import get_session_factory
```

用 `isort` 默认配置（`black` 兼容）。**不要**自己重排，编辑器自动做。

### 1.2 类型提示

- **所有**函数参数与返回值必须有类型提示
- 用 Pydantic v2 `BaseModel` 而不是 `dataclass`（如果需要序列化 / 校验）
- 容器类型用 `list[X]` / `dict[K, V]`（3.9+ 内置）而不是 `List[X]` / `Dict[K, V]`
- Optional 用 `X | None`（3.10+）而不是 `Optional[X]`

```python
# 对
async def get_user(user_id: int) -> User | None: ...

# 错（缺类型）
async def get_user(user_id): ...

# 错（用旧 Optional）
from typing import Optional
async def get_user(user_id: int) -> Optional[User]: ...
```

### 1.3 错误处理

**业务错误**：抛 `HTTPException(detail="...")`，**detail 字段永远是字符串**（前端会显示）。

```python
from fastapi import HTTPException, status

# 对
raise HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail=f"unknown line_id: {line_id}",
)

# 错（detail 是 dict / list——前端处理复杂）
raise HTTPException(status_code=404, detail={"error": "..."})

# 错（裸 Exception——不友好）
raise Exception("not found")
```

**系统错误**：让 FastAPI 默认处理（500），**不要** catch 后重新抛。
**审计 / 后台任务**：`logger.warning(...)` 后**返回**或**continue**（不抛）。

### 1.4 日志

- 用 `from ..core.logging import get_logger`
- 永远 `logger.warning("msg %s", arg)` 而不是 f-string（lazy formatting）
- WARNING 用于"操作员需要知道但不阻塞"
- ERROR 极少用（"系统坏了"）
- INFO 用于"启动时扫到什么" / "完成 1 个周期"
- DEBUG 默认关，需要时再开

```python
# 对
logger.warning("audit_log write failed for %s %s: %s", method, path, exc)

# 错（f-string——无 arg 时也格式化）
logger.warning(f"audit_log write failed for {method} {path}: {exc}")
```

### 1.5 异步 / 同步混用

- 业务代码**优先** `async def`
- sync 函数要调 async，**用 `force_async` shim**（在 `apps/api/app/utils/`）
- **不**用 `asyncio.run()`（会创建新 event loop，与 FastAPI 冲突）

```python
# 对
async def my_endpoint():
    result = await some_async_fn()

# 对（调 sync from async）
result = await run_in_threadpool(some_sync_fn)

# 错（asyncio.run 在 FastAPI 里）
result = asyncio.run(some_async_fn())
```

### 1.6 注释

- **模块顶部 docstring** 必须：说明模块用途、关键不变量、引用相关 commit
- **函数 docstring** 用于公共 API（路由、service 入口），内部 helper 可省
- 注释用中文（已翻译）；标识符用英文
- 复杂正则 / 算法必须注释（why 而不是 what）

```python
"""
apps/api/app/services/copilot_engine.py

The Copilot Engine — turns a free-form Finance BP question into a
structured answer with citations pointing at real data.
"""
```

### 1.7 Pydantic v2 BaseModel

- `model_config = ConfigDict(extra="forbid")` 用于 request body（拒绝未声明字段）
- 用 `Field(default=..., min_length=..., max_length=...)` 而不是 `conint` / `constr`
- 枚举用 `Literal` 而不是 `Enum`（除非需要遍历）

```python
# 对
class CreateAIModelRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    provider: Literal["openai", "deepseek", "ollama", "mock", "anthropic", "custom"]
    
    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

# 错（constr 已被 Pydantic v2 弃用）
from pydantic import constr
name: constr(min_length=1, max_length=64)
```

### 1.8 SQL 手写

- 用 `text("SELECT ...")` + 参数化
- **永远**用 `:param_name` 绑定，**永远不要** f-string 拼 SQL
- 关键字用大写（`SELECT` / `FROM` / `WHERE`）保持可读

```python
# 对
result = await session.execute(
    text("SELECT id, name FROM users WHERE username = :u"),
    {"u": username},
)

# 错（SQL injection）
result = await session.execute(text(f"SELECT id FROM users WHERE username = '{username}'"))
```

### 1.9 命名

| 类型 | 风格 | 例 |
|---|---|---|
| 模块 / 包 | `snake_case` | `alert_engine.py` |
| 类 | `PascalCase` | `AIModelItem` |
| 函数 / 变量 | `snake_case` | `get_active_model` |
| 常量 | `UPPER_SNAKE_CASE` | `DB_BOOTSTRAP_TIMEOUT_S` |
| 私有 | `_leading_underscore` | `_load_user_by_id` |
| Pydantic 字段 | `snake_case` | `api_key_set`（**不要** camelCase） |
| 业务线 id | `kebab-case` | `office-leasing` |
| 环境变量 | `UPPER_SNAKE_CASE` | `BIZ_BP_AI_SECRET_KEY` |

---

## 2. TypeScript 规范

### 2.1 导入顺序

```typescript
// 1. Next.js / React
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { useState, useEffect } from "react";

// 2. 第三方 UI / 库
import { Button, Form, Input } from "antd";
import { Bar } from "@ant-design/charts";

// 3. 共享 packages
import type { BusinessLine, Indicator } from "@biz-bp/types";

// 4. 本地
import { SidebarMenu } from "../_components/SidebarMenu";
import { fetchMyData } from "./api";
```

### 2.2 严格模式

`tsconfig.json` 已开 `strict: true`。**不要**关：
- `any` 禁止（用 `unknown` + 收窄）
- `// @ts-ignore` 禁止（用 `// @ts-expect-error <reason>`）
- `as` 类型断言仅在**完全确定**时用

```typescript
// 对
const data: unknown = await response.json();
if (isAIModelList(data)) {
  // 收窄到 AIModelList
}

// 错
const data: any = await response.json();
data.models.forEach(...);  // 编译通过但运行时炸
```

### 2.3 Interface vs Type

- **interface** 用于对象结构（可被 extends / implements）
- **type** 用于 union / 交叉 / utility 类型

```typescript
// 对
export interface BusinessLine {
  id: string;
  name: string;
  // ...
}

export type IndicatorFormat = "currency" | "number" | "percent" | "ratio";

// 错
export type BusinessLine = {  // 应该用 interface
  id: string;
};
```

### 2.4 Ant Design v5 模式

- 用 hooks 版本（`useState` / `useEffect` / `useForm`）
- 服务端组件**不要**用 antd（Context API 在 RSC 里不可用）
- 需要 antd 的页面**显式**加 `'use client'`

```typescript
// 对（页面顶部）
'use client';
import { Button } from "antd";

// 错（server component 用 antd）
import { Button } from "antd";  // RSC 报错
```

参考 [`docs/cockpit-deliverable.md`](../cockpit-deliverable.md) 的失败 / 修复记录。

### 2.5 BFF 代理

参见 [`extending.md`](extending.md) §3。必备 5 行：

```typescript
export const dynamic = "force-dynamic";
export const revalidate = 0;
const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
// ...
headers: { cookie: request.headers.get("cookie") ?? "" },
```

### 2.6 错误处理

- 用 `try/catch` 包 fetch
- 失败时返回 `NextResponse.json({ detail: "..." }, { status: 502 })`
- **永远不要**在 BFF 里 throw（会让 Next 返回 500 + 整页崩溃）

```typescript
try {
  const upstream = await fetch(url, { ... });
  return new Response(await upstream.arrayBuffer(), { status: upstream.status });
} catch (err) {
  return NextResponse.json(
    { detail: `upstream error: ${String(err)}` },
    { status: 502 },
  );
}
```

---

## 3. 业务线 YAML 规范

### 3.1 字段对齐 Pydantic schema

每个 YAML 字段必须**有**对应的 Pydantic validator。改 schema 要改两边：

| YAML 文件 | Pydantic 模型 | 位置 |
|---|---|---|
| `manifest.yaml` | `BusinessLine` | `apps/api/app/core/registry.py:54-72` |
| `indicators.yaml` | `IndicatorsFile` | `apps/api/app/core/registry.py:95-97` |
| `sensitivity.yaml` | （动态） | `apps/api/app/services/sensitivity_engine.py` |
| `forecast.yaml` | （动态） | `apps/api/app/services/forecast_engine.py` |
| `alerts.yaml` | （动态） | `apps/api/app/services/alert_engine.py` |

### 3.2 命名

- `id` 字段 URL-safe（`[a-z0-9_-]+`）
- `name` 字段人类可读（"住宅分析" / "工业地产部"）
- `line_id` 在 sensitivity/forecast/alerts YAML 里**必须**与 `manifest.id` 一致

### 3.3 时间序列

- `frequency: monthly`（目前唯一支持的值）
- `horizon_months: 12`（预测 12 个月）
- `historical_periods: 24`（回看 24 个月用于 MA / EMA）

### 3.4 告警规则

- `id` 在 line 内**必须**唯一
- `severity` ∈ {`low` / `medium` / `high`}
- `operator` ∈ {`>` `<` `>=` `<=` `==` `between` `change_pct` `consecutive`}
- `enabled: true` 才参与检查

---

## 4. Commit 规范

### 4.1 Commit message 格式

```
<type>(<scope>): <imperative 1-line summary>

<optional 1-3 line body explaining the WHY>

Verification:
- <what you ran>
- <what you saw>
```

### 4.2 Type 前缀

| Type | 用途 | 例 |
|---|---|---|
| `feat` | 新功能 | `feat(scrapers): add real-data lianjia ershoufang fetcher` |
| `fix` | 修 bug | `fix(bff): forward cookie on /api/lines/* catch-all` |
| `chore` | 杂事（依赖、配置） | `chore(deps): pin bcrypt<5 for passlib compat` |
| `docs` | 文档 | `docs(arch): add audit report 2026-09-03` |
| `test` | 测试 | `test(auth): add 43 RBAC tests` |
| `refactor` | 重构（无功能变化） | `refactor(scrapers): extract http_get retry helper` |
| `perf` | 性能 | `perf(api): add asyncpg pool_pre_ping` |
| `style` | 格式 | `style(api): isort + black` |

### 4.3 Scope

| Scope | 范围 |
|---|---|
| `api` | 后端通用 |
| `web` | 前端通用 |
| `bff` | BFF 代理 |
| `rbac` | 身份认证 / 权限 |
| `scrapers` | 爬虫 |
| `copilot` | AI 问答 |
| `forecast` / `sensitivity` / `alerts` | 对应引擎 |
| `business-line:<id>` | 单条业务线（罕见，优先放通用 scope） |
| `docker` / `dbt` / `airflow` | 编排 / 数据 |

### 4.4 验证行

每个 commit message 末尾**强烈建议**包含：
- 跑了什么（`pytest tests/test_xxx.py` / `curl ...` / UI click test）
- 看到了什么（PASS / 200 / "登录成功"）

AI 提交 commit 时这行**必须**真实——不要写"全部通过"如果没跑。

---

## 5. PR 规范

### 5.1 标题

- 50 字符内
- 前缀同 commit type
- 祈使语气

例：
- `fix(bff): forward cookie on /api/lines/* catch-all`
- `feat(scrapers): add lianjia ershoufang real-data fetcher`

### 5.2 描述模板

```markdown
## 改了什么
- ...

## 改的理由
- ...

## 验证
- [x] pytest -q
- [x] curl 冒烟
- [x] UI 点击测试

## 影响
- 需要重启 API: 是 / 否
- 需要 DB 迁移: 是 / 否
- 影响 RBAC: 是 / 否
```

### 5.3 单一职责

一个 PR 改一类事。**不要**把"修 BFF cookie"和"加新业务线"放在同一 PR。

---

## 6. 文档规范

### 6.1 语言

- 新写的 `.md`：**中文**（已 2026-09-03 翻译）
- 新写的 `.py` / `.ts` / `.tsx` 注释：**中文**（已翻译）
- 错误消息 / 日志：**中文优先**
- 状态码 / 字段名 / 命令行 flag：**英文**

### 6.2 file:line 引用

- 用 `path/to/file.py:42` 风格（GitHub UI 可点击）
- 写"在 `apps/api/app/main.py:40`" 而不是"在 main.py 里的 lifespan 那里"

### 6.3 表格

- 复杂对照用 markdown 表格
- 列对齐（用 `|` 多余空格）
- 第一列是"名字 / 字段"，最后一列是"备注"

### 6.4 代码块

- 标注语言（` ```python ` / ` ```typescript ` / ` ```powershell ` / ` ```bash `）
- 复杂命令前用注释说明目的

```powershell
# 重置嵌入式 pgserver（永久删除 .pgdata/）
python apps\api\pgserver_runner.py --reset
```

### 6.5 链接

- 仓库内文档用相对路径：`[operations.md](operations.md)`
- 仓库内代码用 file:line：`apps/api/app/main.py:40`
- 外部资源用完整 URL

---

## 7. Git 规范

### 7.1 分支

| 类型 | 命名 | 例 |
|---|---|---|
| 修 bug | `fix/<short-desc>` | `fix/bff-cookie-forward` |
| 新功能 | `feat/<short-desc>` | `feat/scrapers-policy-realdata` |
| 重构 | `refactor/<short-desc>` | `refactor/copilot-mock-engine` |
| 文档 | `docs/<short-desc>` | `docs/maintenance-handover` |

主分支：`master`（不是 `main`——历史遗留）。

### 7.2 不许做的操作

- `git push --force`（已发布的 31 个 commit 不能动）
- `git reset --hard` 在已推 commit 上
- `git rebase -i` 跨多个 commit 改历史
- 把 `.env` / `.pgdata/` / `node_modules/` commit（已 `.gitignore`）

### 7.3 临时文件的处理

- 调试用的 `xxx_test.py` / `xxx_evidence.txt` 放 `business_lines/<line>/_evidence/` 或类似
  下划线前缀目录
- 完成后**必须** `git rm` 或 `mavis-trash`
- **不要**留 `_check.py` / `_patch_tests.py` / `_test.py` 之类在 `apps/api/`（参见现有
  习惯，已有几个历史文件没清但**不**鼓励继续增加）

---

## 8. 通用规则（总结）

| 规则 | 适用 |
|---|---|
| **永远**用 UTF-8 写文件 | 任何 CJK 内容 |
| **永远**用 `mavis-trash` 或 `py -X utf8 -c "import os; os.remove(...)"` 删文件 | Windows 上 |
| **永远**用 `from __future__ import annotations` | Python 3.12 文件 |
| **永远**用 `export const dynamic = "force-dynamic"` | BFF route |
| **永远**转发 `cookie` header | BFF route |
| **永远**用 `text(...)` + 参数化 | 手写 SQL |
| **永远**用 `logger.warning("msg %s", arg)` 而不是 f-string | 日志 |
| **不要** 写 `business_lines.<id>` 的硬编码 import | `apps/` / `packages/` |
| **不要** 用 `Remove-Item` / `rm -rf` | Windows 上 |
| **不要** 改 `apps/api/app/fin_bp_api.egg-info/` | 任何情况 |
| **不要** `git push` 未经人类授权 | 任何情况 |
| **不要** 重命名 `finbp_token` cookie | 任何情况 |
| **不要** 重命名 `biz-bp-portal` 目录 | 任何情况 |
