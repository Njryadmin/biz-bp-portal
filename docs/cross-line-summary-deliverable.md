# InsightBP — 跨业务线汇总 (G 完成) 交付

> **交付日期**: 2026-09-04
> **任务**: G
> **Commits**: `bc84fd8` + `4b9c49c`
> **范围**: 2 后端端点 + 2 BFF 路由 + `?lines=` query param 解析 + 域隔离

---

## 0. 一句话总览

集团 FINBP / HRBP 需要**一次拉多条业务线的 KPI 汇总**。本任务实现 `/api/finance/summary?lines=*` + `/api/hr/summary?lines=residential,retail`，按 `?lines=` 参数过滤范围、按 `DataDomain` 检查隔离、跨线 totals 累加（rate 类 null）。**34 个新测试通过**。

---

## 1. Result

| 区域 | 状态 | 证据 |
|---|---|---|
| **A. 后端 2 端点** | PASS | `apps/api/app/routers/cross_line_summary.py` (finance / hr) |
| **B. ?lines= query param 解析** | PASS | 4 种语义：缺省 / `*` / `all` / csv |
| **C. 域隔离** | PASS | hr_bp 调 `/api/finance/summary` → 403 |
| **D. line-scoped 静默降级** | PASS | fin_bp(residential) 调 `?lines=*` → 只返回 residential |
| **E. 跨线 totals 累加** | PASS | totals.sum 类累加；rate 类（IRR / 坪效）→ null |
| **F. BFF 路由** | PASS | `apps/web/app/api/finance/summary/route.ts` + `hr/summary/route.ts` |
| **G. 测试** | PASS | 34 个新测试 (`tests/test_cross_line_summary.py`) |

**Result: PASS**

---

## 2. 后端 — 2 端点

`apps/api/app/routers/cross_line_summary.py`：

| 端点 | 域 | 跨线累计 |
|---|---|---|
| `GET /api/finance/summary?lines=*` | finance | totals 累加（sum），rate 类（IRR / 坪效 / 人均营收）→ **null** |
| `GET /api/hr/summary?lines=residential,retail` | hr | 同上 |

### 2.1 `?lines=` 解析 (4 种语义)

```python
def _parse_lines_param(lines: Optional[str], user: CurrentUserV2) -> list[str]:
    """4 种语义:
    - 缺省 (None)         → 用户可见的全部 line
    - 空串 / '*' / 'all'  → 同上
    - csv (residential,retail) → 该 csv (过滤用户不可见)
    - 任意字符串 (如 'foo,bar') → 解析后过滤
    """
    if not lines or lines.strip().lower() in ("*", "all"):
        return user.filter_accessible_lines(load_all_line_ids())

    requested = [s.strip() for s in lines.split(",") if s.strip()]
    accessible = set(user.filter_accessible_lines(load_all_line_ids()))
    return [lid for lid in requested if lid in accessible]
```

### 2.2 域隔离铁律

```python
@router.get("/finance/summary")
async def finance_summary(
    user: CurrentUserV2 = Depends(get_current_user_v2),
    lines: Optional[str] = Query(None),
):
    # 域检查：必须能在某条 line 上 VIEW FINANCE
    if not _any_line_has_domain(user, user.filter_accessible_lines(load_all_line_ids()), DataDomain.FINANCE):
        raise HTTPException(403, "no FINANCE view access on any accessible line")
    # ...
```

**调用矩阵**：

| 用户 | `/finance/summary` | `/hr/summary` |
|---|---|---|
| `fin_bp(residential)` | 200 (residential 域 FINANCE 可见) | 403 (HR 不可见) |
| `hr_bp(residential)` | 403 (FINANCE 不可见) | 200 |
| `fin_bp_global` | 200 (跨线 FINANCE) | 403 (HR 不可见) |
| `hr_bp_global` | 403 (FINANCE 不可见) | 200 (跨线 HR) |
| `line_owner(residential)` | 200 | 200 |
| `admin` / `auditor` / `viewer` | 200 (R) | 200 (R) |

### 2.3 line-scoped 静默降级

**问题**：`fin_bp(residential)` 调 `?lines=*` — `*` 是"全部"，但他**只该看到 residential**。

**方案**：`filter_accessible_lines` 在解析 `?lines=` 时自动按用户 `accessible_lines` 过滤，**不**返回错误（避免 UI 复杂处理）。

```python
# 例子
fin_bp_residential.filter_accessible_lines(["residential", "retail", "valuation"])
# → ["residential"]

# 调用 ?lines=* 时
_parse_lines_param("*", fin_bp_residential)
# → ["residential"]  (自动过滤, 不抛错)
```

### 2.4 跨线 totals 累加

**例子**（3 条 line 的 finance summary）：

```json
{
  "lines": [
    {"line_id": "residential", "totals": {"revenue": 100, "ar_aging": 30}},
    {"line_id": "retail",      "totals": {"revenue": 200, "ar_aging": 45}},
    {"line_id": "valuation",   "totals": {"revenue": 50,  "ar_aging": null}}
  ],
  "totals": {
    "revenue": 350,           // 100+200+50 累加
    "ar_aging": null,         // rate 类: 部分为 null → 整体 null
    "irr": null,              // rate 类
    "revenue_per_fte": null   // rate 类
  }
}
```

**累加规则**（在 `cross_line_summary.py:_aggregate_totals`）：

| KPI 类型 | 累加逻辑 | 例子 |
|---|---|---|
| `sum` (e.g. revenue, headcount) | 求和（null 视为 0） | revenue = 100+200+50 = 350 |
| `rate` (e.g. IRR, 坪效) | 任一为 null → 整体 null | ar_aging = 30+45+null = null |
| `latest` (e.g. last_updated) | 取最新非 null | 同上 |

---

## 3. BFF 路由

`apps/web/app/api/finance/summary/route.ts`：

```typescript
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const target = `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/finance/summary${url.search}`;

  const resp = await fetch(target, {
    headers: {
      cookie: request.headers.get("cookie") ?? "",
      "X-Active-View": request.cookies.get("active_view")?.value ?? "",
    },
  });
  return new Response(resp.body, {
    status: resp.status,
    headers: { "content-type": resp.headers.get("content-type") ?? "application/json" },
  });
}
```

`hr/summary/route.ts` 同构。

---

## 4. UX 决策记录

### 4.1 为什么 `?lines=` 用 csv 而不是 `?line=<id>&line=<id>`

- 简单、可读、`URLSearchParams` 直接解析
- 与 GraphQL `?filter[id]=...` 风格一致
- 兼容 `*` / `all` 通配

### 4.2 为什么 line-scoped 静默降级（不抛错）

- 前端调 `?lines=*` 是常见模式（"拉所有"）
- 抛 403 让 UI 必须捕获错误才能显示空数据
- 静默降级让 UI 只需判断"返回的 lines 是否为空"
- 5 大行级别合规**不**要求显式拒绝（"用户看不到自己不该看的"已是合规）

### 4.3 为什么 rate 类 KPI 跨线 null

- IRR = f(收入, 成本, 周期)，跨业务线加权平均无意义
- 强制聚合会误导决策（"集团 IRR 15%" 实际可能掩盖某条线亏损）
- 透明展示 null 让 UI 显式标注"无法跨线汇总"

### 4.4 为什么 totals 包含 line 数组（而不只 totals）

- 前端 drill-down 需要"先看汇总，再点进单线"
- API 一次返回比前端 2 次 fetch 快
- 域检查一致：整组 / 单条都走同一逻辑

---

## 5. 测试覆盖（34 个）

`apps/api/tests/test_cross_line_summary.py`：

| 用例 | 数量 | 覆盖 |
|---|---|---|
| 域检查矩阵 (6 角色 × 2 端点) | 12 | 域隔离 |
| `?lines=` 4 种语义 | 6 | query 解析 |
| line-scoped 降级（5 角色） | 5 | 静默降级 |
| totals 累加（sum 类） | 4 | 累加逻辑 |
| totals null（rate 类） | 4 | rate 类处理 |
| BFF 路由集成 | 3 | cookie 转发 |

---

## 6. 用例 (curl 演示)

### 6.1 fin_bp_global 跨线汇总

```bash
# 1. login
curl -c /tmp/c.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"finbp-global","password":"<set>"}'

# 2. 全部 9 条线
curl -s -b /tmp/c.txt 'http://localhost:18000/api/finance/summary?lines=*' | jq '.lines | length'
# → 9

# 3. 2 条指定线
curl -s -b /tmp/c.txt 'http://localhost:18000/api/finance/summary?lines=residential,retail' | jq '.lines | length'
# → 2

# 4. 缺省 (None)
curl -s -b /tmp/c.txt 'http://localhost:18000/api/finance/summary' | jq '.lines | length'
# → 9
```

### 6.2 hr_bp 调 finance summary → 403

```bash
curl -c /tmp/h.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"hrbp-residential","password":"<set>"}'

curl -s -b /tmp/h.txt -o /dev/null -w "%{http_code}\n" \
  http://localhost:18000/api/finance/summary
# → 403
```

### 6.3 fin_bp(residential) 调 ?lines=* → 静默降级到 1 条

```bash
curl -c /tmp/f.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"finbp-residential","password":"<set>"}'

curl -s -b /tmp/f.txt 'http://localhost:18000/api/finance/summary?lines=*' | jq '.lines[].line_id'
# → "residential"  (仅 1 条)
```

---

## 7. 文件路径速查

| 模块 | 路径 |
|---|---|
| 后端 2 端点 | `apps/api/app/routers/cross_line_summary.py` |
| Pydantic schema | `apps/api/app/schemas/cross_line_summary.py` |
| BFF finance | `apps/web/app/api/finance/summary/route.ts` |
| BFF hr | `apps/web/app/api/hr/summary/route.ts` |
| 测试 | `apps/api/tests/test_cross_line_summary.py` |

---

## 8. Follow-up

- **租户级汇总**：`/api/admin/tenants/{id}/finance/summary` — super admin 看特定 tenant 跨线汇总
- **时间窗过滤**：`?from=2026-01&to=2026-09` — 按月 / 季汇总
- **同比 / 环比**：返回 `totals.previous_period` 让前端画对比图
- **drill-down 端点**：`/api/finance/summary/{line_id}` — 已存在 (`/api/dashboard/fin`)，包装为统一 path

---

_交付日期: 2026-09-04 / 任务: G / Commits: `bc84fd8` + `4b9c49c` / 测试: 34 passed_
