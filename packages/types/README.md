# packages/types — 共享 TypeScript 类型

> 跨 `apps/web` 与未来 `apps/api` 客户端代码的 TypeScript 类型集合。
> **设计目标**：Pydantic schema → TypeScript 类型的"手写同步层"。
> 配套：[`docs/maintenance/conventions.md`](../../docs/maintenance/conventions.md) §6.4。

---

## 为什么有这个包

后端用 Pydantic v2 定义所有 response / request body。前端如果自己重新写
TypeScript 接口，会出现"两边漂移"——后端改了字段名，前端编译不报错但运行时炸。

这个包提供：
- **手写**的 TS 接口，与 Pydantic 模型**语义同步**
- 一个集中位置（`packages/types/src/index.ts`）让两边都引用
- 未来用 `openapi-typescript` 自动生成的**入口**（`pnpm gen:api-types` 之类的命令）

当前是**手写**。自动生成是后续工作（已记录在 `apps/api` 文档的 TODO 里）。

---

## 目录

```
packages/types/
├── package.json
├── tsconfig.json
└── src/
    └── index.ts           ← 全部类型
```

只有一个文件 `src/index.ts`，全部接口 / 类型从这里导出。
这避免了"散落 50 个文件"——共享类型应该**集中**。

---

## 当前包含的类型

### 业务线（mirror `apps/api/app/core/registry.py`）

```typescript
export interface BusinessLineNavItem {
  path: string;
  title: string;
}

export interface BusinessLineWarehouse {
  schema: string;
  dbt_schema: string;
  mart_schema: string;
}

export interface BusinessLineRefresh {
  schedule: string;
  enabled: boolean;
}

export interface BusinessLineFeatures {
  universal_kpi: boolean;
  universal_chart: boolean;
  ag_grid: boolean;
}

export interface BusinessLine {
  id: string;
  name: string;
  display_name?: string;     // 后端额外算
  version: string;
  description: string;
  owner: string;
  icon: string;
  nav: BusinessLineNavItem[];
  api_prefix: string;
  warehouse: BusinessLineWarehouse;
  refresh: BusinessLineRefresh;
  features: BusinessLineFeatures;
  indicators_count?: number;  // 后端额外算
}
```

### 指标（mirror `apps/api/app/schemas/kpi.py`）

```typescript
export type IndicatorFormat = "currency" | "number" | "percent" | "ratio";
export type IndicatorAggregation =
  | "sum" | "avg" | "count" | "count_distinct" | "min" | "max";

export interface Indicator {
  id: string;
  title: string;
  unit: string;
  format: IndicatorFormat;
  aggregation: IndicatorAggregation;
  source: string;
  description: string;
}

export interface KpiValue {
  indicator_id: string;
  value: number | null;
  period_start?: string;
  period_end?: string;
  unit?: string;
}

export interface KpiResponse {
  line_id: string;
  items: KpiValue[];
}
```

### 图表

```typescript
export type ChartType = "line" | "bar" | "pie" | "area";

export interface ChartSpec {
  id: string;
  title: string;
  type: ChartType;
  x: string;
  y: string[];
  source: string;
  description: string;
}
```

---

## 加新类型

### 1. 后端先加 Pydantic

`apps/api/app/schemas/<feature>.py`：

```python
from pydantic import BaseModel
from typing import Optional

class NewFeature(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
```

### 2. 同步到本包

`packages/types/src/index.ts` 加：

```typescript
export interface NewFeature {
  id: number;
  name: string;
  description?: string;
}
```

### 3. 前端引用

```typescript
import type { NewFeature } from "@biz-bp/types";

async function fetchNewFeature(): Promise<NewFeature> {
  const res = await fetch("/api/new-feature");
  return res.json();
}
```

### 4. 验证

```powershell
cd apps\web
npx tsc --noEmit
```

---

## 与 `packages/ui` 的关系

`packages/ui` 是 React 组件（`UniversalKpiCard` 等），**消费** `packages/types`。
例如：

```typescript
// packages/ui/src/UniversalKpiCard.tsx
import type { Indicator, KpiValue } from "@biz-bp/types";

export interface UniversalKpiIndicator extends Indicator {
  value?: KpiValue["value"];
}
```

所以**类型先于组件**——`packages/types` 是叶子，`packages/ui` 依赖它。

---

## 何时用共享类型 vs 页面本地接口

| 场景 | 用 |
|---|---|
| API 响应 / 请求体 | **共享**（`@biz-bp/types`） |
| 跨多个页面用的数据结构 | **共享** |
| 单页面 / 单组件的内部 state | **本地**（`useState<X>` 用 inline interface） |
| Form 临时数据 | **本地** |
| 仅某个 `packages/ui` 组件用 | 放 `packages/ui/src/<component>.tsx` 的 `interface XxxProps` |

原则：**先考虑共享**——如果"这个 X 是不是只在这一个地方用"答不上来，就放共享。
未来加新页面引用时不用改 X。

---

## 字段命名约定

- 跟 Pydantic 一样 `snake_case`（**不要** 改成 `camelCase`）
- API 协议本来就 snake_case
- 前端组件内用 `camelCase`（React 习惯），但**API 边界**必须是 `snake_case`
- TS 接口字段跟 JSON 字段 1-to-1

```typescript
// 对
export interface AIModelItem {
  id: number;
  api_key_set: boolean;          // 跟 Pydantic 完全一致
  is_default: boolean;
}

// 错（让前端组件用 camelCase，转换层会越来越乱）
export interface AIModelItem {
  id: number;
  apiKeySet: boolean;
  isDefault: boolean;
}
```

---

## TypeScript 配置

`packages/types/tsconfig.json`：

- `strict: true`
- `declaration: true`（生成 `.d.ts` 给下游消费）
- `target: ES2022`
- `module: ESNext`

---

## 验证

每次改 `src/index.ts`：

```powershell
# 1. 编译
cd packages\types
npx tsc --noEmit

# 2. 验证 web 能消费
cd ..\..\apps\web
npx tsc --noEmit
```

如果 `apps/web` 编译失败 → 类型不匹配某个用法。先修用法再回头改类型。
**不要**为了一时方便加 `as any`——会让类型保护失效。

---

## 未来工作（TODO）

- 用 `openapi-typescript` 自动从 FastAPI 的 `/openapi.json` 生成大部分类型
- 仅手动维护"openapi 不直接表达"的部分（如 union / 派生类型）
- 加 CI 检查：每次 backend 改 Pydantic schema，CI 自动跑 `pnpm gen:api-types` +
  diff，如果漂移就 fail

**当前不做**——手写已经能 cover。规模起来再自动化。
