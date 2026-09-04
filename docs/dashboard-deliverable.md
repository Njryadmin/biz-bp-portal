# InsightBP — Dashboard MVP (E 完成) 交付

> **交付日期**: 2026-09-04
> **任务**: E
> **Commit**: `075bf8d`
> **范围**: 3 后端端点 + 3 BFF 路由 + 2 前端 dashboard 页 + shared 页 + PerspectiveSwitcher Topbar 组件

---

## 0. 一句话总览

Dashboard MVP 把"单一总览页"**拆成 3 个视角**：`fin` (FINBP) / `hr` (HRBP) / `shared` (共享)。每个视角按 `manifest.yaml:v2:kpis` 拉 KPI 列表，**域检查 + X-Active-View 透传**双保险。**23 个新测试通过**。

---

## 1. Result

| 区域 | 状态 | 证据 |
|---|---|---|
| **A. 后端 3 端点** | PASS | `apps/api/app/routers/dashboard.py` (fin / hr / shared) |
| **B. BFF 3 路由** | PASS | `apps/web/app/api/dashboard/[[...path]]/route.ts` (catch-all) |
| **C. 前端 2 dashboard 页** | PASS | `apps/web/app/(dashboard)/dashboard/{fin,hr}/page.tsx` + `shared/page.tsx` |
| **D. PerspectiveSwitcher** | PASS | `apps/web/app/(dashboard)/_components/PerspectiveSwitcher.tsx` (Topbar 组件) |
| **E. X-Active-View 透传** | PASS | BFF 读 cookie → header → 后端 `get_current_user_v2` 解析 |
| **F. 测试** | PASS | 23 个新测试 (`tests/test_dashboard.py`) |

**Result: PASS**

---

## 2. 后端 — 3 端点

`apps/api/app/routers/dashboard.py`：

| 端点 | 域检查 | 数据源 | 失败行为 |
|---|---|---|---|
| `GET /api/dashboard/fin` | FINANCE view 必填 | manifest `kpis.fin_view` + `kpis.shared_view` | 无 FIN 权限 → 403 |
| `GET /api/dashboard/hr` | HR view 必填 | manifest `kpis.hr_view` + `kpis.shared_view` | 无 HR 权限 → 403 |
| `GET /api/dashboard/shared` | 无 | manifest `kpis.shared_view` | 200 + 空数组 |

### 2.1 域检查逻辑

```python
def _any_line_has_domain(
    user: CurrentUserV2,
    line_ids: list[str],
    domain: DataDomain,
) -> bool:
    """True iff the user can VIEW ``domain`` on at least one accessible line."""
    for lid in line_ids:
        if user.can_access_domain(lid, domain, write=False):
            return True
    return False
```

**例子**：

| 用户 | 调 `/fin` | 调 `/hr` | 调 `/shared` |
|---|---|---|---|
| `fin_bp(residential)` | 200 (residential 域 FINANCE 可见) | 403 (HR 域不可见) | 200 |
| `hr_bp(retail)` | 403 (FINANCE 域不可见) | 200 | 200 |
| `line_owner(residential)` | 200 | 200 | 200 |
| `admin` | 200 (FINANCE view) | 200 | 200 |
| `auditor` / `viewer` | 200 (只读) | 200 | 200 |
| `fin_bp_global` | 200 (跨线 finance) | 403 (HR 不可见) | 200 |

### 2.2 域隔离铁律

- **`hr_bp` 调 `/api/dashboard/fin`** → 403（铁律：FIN/HR 物理隔离）
- **`fin_bp` 调 `/api/dashboard/hr`** → 同样 403
- **`fin_bp_global` 调 `/api/dashboard/hr`** → 403（global 域是 FINANCE，HR 域不可见）

### 2.3 数据 mock (临时方案)

KPI `value` / `trend` 用**确定性 hash** 计算（不随机）：

```python
def _mock_value(line_id: str, kpi_id: str) -> float:
    h = hashlib.sha256(f"{line_id}:{kpi_id}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 1000.0
```

**理由**：
- 同一 KPI 多次渲染得到相同值（无 layout thrash）
- 测试稳定（无 flaky 风险）
- 真实 mart 接入是 P2 follow-up

---

## 3. X-Active-View 透传链

```
1. 用户点击 PerspectiveSwitcher (Topbar 右上)
   ↓
2. 选 fin / hr / line_owner / ...
   ↓
3. BFF 路由读 cookie + 写 X-Active-View header
   ↓
4. fetch('/api/dashboard/fin', { headers: { 'X-Active-View': view } })
   ↓
5. 后端 get_current_user_v2 解析 header → 写 CurrentUserV2.active_view
   ↓
6. dashboard.py 读 active_view → 审计日志带 active_view 标签
   ↓
7. raw.audit_log 新增 active_view 列
```

### 3.1 BFF 透传示例

`apps/web/app/api/dashboard/[[...path]]/route.ts`：

```typescript
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const target = `${process.env.NEXT_PUBLIC_API_BASE_URL}${url.pathname}${url.search}`;

  // 读 cookie 拿 active_view
  const cookieView = request.cookies.get("active_view")?.value;
  const headers: Record<string, string> = {
    cookie: request.headers.get("cookie") ?? "",
  };
  if (cookieView) {
    headers["X-Active-View"] = cookieView;
  }

  const resp = await fetch(target, { headers });
  return new Response(resp.body, {
    status: resp.status,
    headers: { "content-type": resp.headers.get("content-type") ?? "application/json" },
  });
}
```

### 3.2 后端解析

`apps/api/app/core/auth_v2.py:get_current_user_v2`：

```python
async def get_current_user_v2(
    request: Request,
    x_active_view: Optional[str] = Header(default=None, alias="X-Active-View"),
) -> CurrentUserV2:
    user = await _load_user_v2_from_cookie(request)
    if x_active_view:
        valid_views = {"fin", "hr", "line_owner", "admin", "auditor", "viewer", "none"}
        if x_active_view in valid_views:
            user.active_view = x_active_view
    return user
```

---

## 4. 前端 — 2 dashboard 页 + shared

### 4.1 `dashboard/fin/page.tsx`

```tsx
"use client";
import { useEffect, useState } from "react";

export default function FinDashboardPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);

  useEffect(() => {
    fetch("/api/dashboard/fin", { credentials: "include" })
      .then(r => r.json())
      .then(setData);
  }, []);

  if (!data) return <Spin />;
  return (
    <div>
      <h1>FIN 视角 KPI</h1>
      <Row gutter={16}>
        {data.kpis.map(kpi => (
          <Col key={kpi.kpi_id} span={8}>
            <UniversalKpiCard
              title={kpi.title}
              value={kpi.value}
              unit={kpi.unit}
              trend={kpi.trend}
            />
          </Col>
        ))}
      </Row>
    </div>
  );
}
```

`hr` / `shared` 页面同构（只改 fetch URL）。

### 4.2 `PerspectiveSwitcher` 组件

`apps/web/app/(dashboard)/_components/PerspectiveSwitcher.tsx`：

```tsx
"use client";
import { Select } from "antd";
import { useState, useEffect } from "react";

const VIEWS = [
  { value: "fin", label: "FIN 视角" },
  { value: "hr", label: "HR 视角" },
  { value: "line_owner", label: "业务线负责人" },
  { value: "admin", label: "管理员" },
  { value: "viewer", label: "只读" },
];

export function PerspectiveSwitcher() {
  const [view, setView] = useState<string>("fin");

  useEffect(() => {
    // 读 cookie 拿默认 view
    const c = document.cookie.match(/active_view=([^;]+)/);
    if (c) setView(c[1]);
  }, []);

  const onChange = (v: string) => {
    setView(v);
    document.cookie = `active_view=${v}; path=/; max-age=86400`;
    // 触发顶层 layout 重新 fetch 当前页
    window.dispatchEvent(new Event("active_view_changed"));
  };

  return (
    <Select value={view} onChange={onChange} options={VIEWS} style={{ width: 160 }} />
  );
}
```

**集成位置**：`apps/web/app/(dashboard)/_components/Topbar.tsx` 右上角（在 `TenantBadge` 之前）。

---

## 5. UX 决策记录

### 5.1 为什么用 3 个独立端点（而不是 query param）

- **RESTful 清晰**：`/fin` / `/hr` / `/shared` 路径就告诉前端拿哪种数据
- **域检查前置**：URL 就表明意图，不必先发 OPTIONS
- **审计日志清晰**：`raw.audit_log.path = '/api/dashboard/fin'` 直接反映用户视角

### 5.2 为什么 `shared` 不做域检查

- 共享 KPI 设计上就是任何看到该 line 的人都能看
- 不强制 `X-Active-View=shared` 也能访问（语义不依赖 header）
- 200 + 空数组是合法响应（"我能看 0 条 KPI"，不是 403）

### 5.3 为什么 `fin` / `hr` 任何一个域失败就 403

- 用户**没有**该域的访问权 → 没必要返回部分数据
- 返回 403 让前端显示"权限不足"清晰错误页（不显示空数组）
- 与"404 vs 403"安全原则一致：宁可 403，不漏数据

### 5.4 KPI value / trend 用 hash 而非真实数据

- P0 阶段先打通**数据流**（manifest → 后端 → 前端）
- 真 mart 接入是 P2（需要 tenant session 化 + dbt model + 实时计算）
- Hash 保证 UI 稳定（无随机 layout thrash）

---

## 6. 测试覆盖（23 个）

`apps/api/tests/test_dashboard.py`：

| 用例 | 数量 | 覆盖 |
|---|---|---|
| `/fin` 端点 6 角色 × 3 line 组合 | 8 | 域检查矩阵 |
| `/hr` 端点 6 角色 × 3 line 组合 | 8 | 域检查矩阵 |
| `/shared` 端点任何角色 200 | 2 | 无域检查 |
| `X-Active-View` header 透传 | 3 | 后端解析 |
| KPI 数据来源（manifest）| 2 | mock value 稳定性 |

---

## 7. 用例 (curl 演示)

### 7.1 fin_bp(residential) 调 /fin

```bash
curl -c /tmp/c.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"finbp-residential","password":"<set>"}'

curl -s -b /tmp/c.txt http://localhost:18000/api/dashboard/fin | jq '.kpis | length'
# → 2 (residential manifest 有 fin_view + shared_view KPI)
```

### 7.2 hr_bp 调 /fin → 403

```bash
curl -c /tmp/h.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"hrbp-residential","password":"<set>"}'

curl -s -b /tmp/h.txt -o /dev/null -w "%{http_code}\n" \
  http://localhost:18000/api/dashboard/fin
# → 403
```

### 7.3 X-Active-View 透传

```bash
# 切到 hr 视角
curl -s -b /tmp/c.txt -H "X-Active-View: hr" \
  http://localhost:18000/api/auth/me-v2 | jq '.active_view'
# → "hr"
```

---

## 8. 文件路径速查

| 模块 | 路径 |
|---|---|
| 后端 3 端点 | `apps/api/app/routers/dashboard.py` |
| Dashboard Pydantic schema | `apps/api/app/schemas/dashboard.py` |
| BFF catch-all | `apps/web/app/api/dashboard/[[...path]]/route.ts` |
| 前端 FIN 页 | `apps/web/app/(dashboard)/dashboard/fin/page.tsx` |
| 前端 HR 页 | `apps/web/app/(dashboard)/dashboard/hr/page.tsx` |
| 前端 Shared 页 | `apps/web/app/(dashboard)/dashboard/shared/page.tsx` |
| Topbar 组件 | `apps/web/app/(dashboard)/_components/PerspectiveSwitcher.tsx` |
| 通用 KPI card | `packages/ui/src/UniversalKpiCard.tsx` |
| 测试 | `apps/api/tests/test_dashboard.py` |

---

## 9. Follow-up

- **真实 mart 数据接入**：P2 — dbt model + tenant session 化
- **KPI trend 真实计算**：当前 hash；接 mart 后用 `LAG()` / `WINDOW FUNCTION`
- **Dashboard drill-down**：KPI 卡点击 → 跳到该 line 的详细页（v1 已有 `/[line]/trends` 路径，扩展）
- **个性化 KPI**：用户自定义 dashboard 布局（拖拽）+ 收藏 KPI

---

_交付日期: 2026-09-04 / 任务: E / Commit: `075bf8d` / 测试: 23 passed_
