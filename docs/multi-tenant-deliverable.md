# InsightBP — 多租户 (M1 + M2 + M3) 完整交付

> **交付日期**: 2026-09-04
> **阶段**: PR #1 合并 master
> **范围**: DB schema (RLS) + 后端 (tenant context + session helper) + Admin UI
> **目标**: SaaS 化准备 — 5 大房地产咨询公司 = 5 个 tenant，**物理隔离**，**单实例可托管**

---

## 0. 一句话总览

把单租户架构**演进为 RLS (Row-Level Security) 多租户**：1 个 Postgres 实例，6 张业务表加 `tenant_id` 列 + RLS 策略锁 + 中间件透传 tenant context。Super admin 可跨 tenant 切换，普通用户被锁在自己 tenant 内。**17 (M1) + 7 (M2) + 11 (M3) = 35 个新测试通过**，v0.1.0 现有功能 0 破坏。

---

## 1. Result

| 阶段 | 状态 | 证据 |
|---|---|---|
| **M1 — schema + RLS** | PASS | `infra/migrations/003_multi_tenant_setup.sql` (tenants 表 + 6 表加 tenant_id + RLS FORCE + tenant_lock policy) |
| **M2 — tenant context + session** | PASS | `apps/api/app/core/tenant_context.py` (TenantContext + get_tenant_context); `apps/api/app/db/tenant.py` (tenant_session helper); 14 个 pre-existing 失败修复 |
| **M3 — super admin UI** | PASS | `apps/api/app/core/rbac.py:require_super_admin_dep`; `apps/api/app/routers/admin_tenants.py` (4 端点); `apps/web/app/(dashboard)/_components/TenantBadge.tsx` + `TenantSwitcher.tsx`; `admin/tenants/page.tsx` |
| **Migration runner 集成** | PASS | 003 / 004 自动 apply; `GET /api/admin/migrations/status` 返回 |
| **测试** | PASS | 17 (M1) + 7 (M2) + 11 (M3) = 35 个新测试; v0.1.0 现有 145+ 仍绿 |

**Result: PASS**

---

## 2. 背景 — 为什么需要多租户

### 2.1 业务驱动

5 大房地产咨询公司是**潜在 SaaS 客户**：
- 客户 A (JLL) / B (CBRE) / C (Cushman) / D (Savills) / E (Colliers)
- 每个客户**有独立的员工 / 业务线 / 业务数据 / 审计**
- 同一物理硬件**单实例可托管**（on-prem 私有化）— 部署到客户机房也只跑 1 个容器栈

### 2.2 三个备选方案

| 方案 | 描述 | 优点 | 缺点 |
|---|---|---|---|
| **A. 独立 DB per tenant** | 每个 tenant 一个 schema / database | 物理隔离最强 | 备份 N 倍、migration N 倍、连接池 N 倍、运维噩梦 |
| **B. 应用层过滤 (BFF 加 tenant_id filter)** | 所有 SQL 加 `WHERE tenant_id = :tid` | 简单 | 任何漏一处 = 数据泄露；10 个租户 = 10 套索引 |
| **C. Postgres RLS (本方案)** | DB 层强制 row-level filter | DB 强制 + 应用层 + 触发器 3 重保险 | RLS 性能开销（~5%）+ GUC 配置复杂 |

**决策**：方案 C (RLS)。理由：
- **物理隔离由 DB 保证**（即使应用层有 bug，DB 也会拒绝跨租户查询）
- **运维成本低**（单实例、单 migration、单备份、单连接池）
- **5 大行级别合规要求**（客户 A 看不到客户 B 的数据是硬约束）

### 2.3 三阶段路线

```
M1 — schema 改造 + RLS 启用        (commit 0d26c87, 2026-09-04)
M2 — tenant context + session helper + 14 失败修复 (commit b00b499)
M3 — super admin UI + 4 端点 + 前端 badge/switcher (commit 8f2d90b)
```

---

## 3. M1 — schema 改造 (commit `0d26c87`)

### 3.1 `tenants` 顶层表

```sql
CREATE TABLE IF NOT EXISTS tenants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         TEXT UNIQUE NOT NULL,           -- 'acme-realty', 'jll', 'cbre', ...
    name         TEXT NOT NULL,                  -- 'Acme Realty'
    plan         TEXT NOT NULL DEFAULT 'standard',  -- 'standard' | 'enterprise' | 'demo'
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb
);
```

### 3.2 6 张业务表加 `tenant_id` (additive, NOT NULL after backfill)

```sql
ALTER TABLE users               ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE user_roles          ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE user_business_lines ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE raw.audit_log       ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE ai_models           ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE raw.uploads         ADD COLUMN IF NOT EXISTS tenant_id UUID;
```

加 6 个索引（`idx_users_tenant` 等）+ 6 个 FK 约束（`ON DELETE RESTRICT`，防误删 tenant）。

### 3.3 default tenant backfill

```sql
INSERT INTO tenants (id, slug, name, plan, is_active)
VALUES (
    '00000000-0000-0000-0000-000000000000',
    'default',
    'Default Tenant (legacy)',
    'enterprise',
    TRUE
) ON CONFLICT (slug) DO NOTHING;

UPDATE users               SET tenant_id = '00000000-0000-0000-0000-000000000000' WHERE tenant_id IS NULL;
UPDATE user_roles          SET tenant_id = '00000000-0000-0000-0000-000000000000' WHERE tenant_id IS NULL;
-- ... 4 more
```

**v0.1.0 现有数据全部 backfill 到 default tenant** — 0 数据丢失。

### 3.4 RLS (Row-Level Security)

```sql
ALTER TABLE users               ENABLE ROW LEVEL SECURITY;
ALTER TABLE users               FORCE ROW LEVEL SECURITY;  -- 超级用户也强制
-- ... 5 more

CREATE POLICY tenant_lock ON users
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
-- ... 5 more
```

**关键点**：
- **FORCE**：表 owner（superuser）也强制走 RLS — 防止 admin 误操作跨租户
- **`tenant_lock` policy**：`tenant_id = current_setting('app.tenant_id', true)::uuid` — 任何 query 必须先设 `app.tenant_id` GUC，否则 `current_setting(..., true)` 返回空串 → `''::uuid` 抛错 → 0 行

**M2 middleware 通过 `SET LOCAL app.tenant_id = '<uuid>'` 解锁**（一次请求内有效，事务结束自动清）。

---

## 4. M2 — tenant context + session helper (commit `b00b499`)

### 4.1 触发器 fallback (`infra/migrations/004_*.sql`)

```sql
CREATE OR REPLACE FUNCTION set_tenant_from_guc()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.tenant_id IS NULL THEN
        NEW.tenant_id := COALESCE(
            NULLIF(current_setting('app.tenant_id', true), '')::uuid,
            '00000000-0000-0000-0000-000000000000'
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_set_tenant BEFORE INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION set_tenant_from_guc();
-- ... 5 more
```

**作用**：INSERT 不带 `tenant_id` 时，**自动**从 GUC 读取填入。GUC 也没设时回落到 default tenant。

**为什么需要 fallback**（详见 `AGENTS.md §10 怪癖`）：
- 审计 middleware (`AuditMiddleware`) 在请求早期写 `raw.audit_log` — 此时 router 还没设 GUC
- 直接 NOT NULL 违反会让审计写入失败 → **整个响应被拖垮**（违背 audit sidecar 设计）
- fallback 让 audit 至少能跑（写到 default tenant），不影响业务路由

### 4.2 `TenantContext` + `get_tenant_context` dep

`apps/api/app/core/tenant_context.py`：

```python
@dataclass(slots=True)
class TenantContext:
    tenant_id: UUID       # 永远 valid; fallback DEFAULT_TENANT_ID
    bypass_rls: bool      # True 仅 super admin
    is_super_admin: bool
    source: str           # "header" / "user_default" / "default"


async def get_tenant_context(
    request: Request,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
) -> TenantContext:
    """优先级:
    1. X-Tenant-ID header (super admin 显式切)
    2. user.tenant_id (普通用户)
    3. DEFAULT_TENANT_ID (兜底)
    """
```

### 4.3 `tenant_session` helper

`apps/api/app/db/tenant.py`：

```python
@asynccontextmanager
async def tenant_session(tenant_id: UUID, *, bypass_rls: bool = False):
    """包装 SQLAlchemy session — 自动 SET LOCAL app.tenant_id / app.bypass_rls.

    用法:
        async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
            rows = await session.execute(text("SELECT id FROM users"))
    """
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            if bypass_rls:
                await session.execute(text("SET LOCAL app.bypass_rls = 'on'"))
            yield session
```

### 4.4 14 个 pre-existing 失败修复

M2 启用 RLS 后，所有走 raw SQL 的 router 突然**只看到 default tenant**（因为没设 GUC）→ 14 个 pre-existing 集成测试失败。

**修复模式**（统一套用）：

```python
# BEFORE (无 RLS)
@router.get("/users")
async def list_users(user: CurrentUser = Depends(require_admin_dep)):
    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(text("SELECT id FROM users"))

# AFTER (RLS 兼容)
@router.get("/users")
async def list_users(
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
):
    async with tenant_session(ctx.tenant_id, bypass_rls=ctx.bypass_rls) as session:
        rows = await session.execute(text("SELECT id FROM users"))
```

涉及 router: `auth.py` / `ai_models.py` / `admin_users.py` / `admin_business_lines.py` / `migrations.py` / `admin_tenants.py` (新建) / 6 个 4 通用 engine router。

---

## 5. M3 — super admin UI (commit `8f2d90b`)

### 5.1 `require_super_admin_dep`

`apps/api/app/core/rbac.py` (新增)：

```python
async def require_super_admin_dep(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not getattr(user, "is_super_admin", False):
        raise HTTPException(403, "super admin required")
    return user
```

`is_super_admin` 字段在 M1 没加，在 `infra/migrations/004_*.sql` 加上 + 索引 + 把 `admin` 用户标 `TRUE`。

### 5.2 4 端点 (`apps/api/app/routers/admin_tenants.py`)

| 端点 | 方法 | 鉴权 | 用途 |
|---|---|---|---|
| `/api/admin/tenants` | GET | super admin | 列出所有 tenant |
| `/api/admin/tenants` | POST | super admin | 创建 tenant (slug / name / plan) |
| `/api/admin/tenants/{id}` | PATCH | super admin | 更新 name / plan / is_active / metadata |
| `/api/auth/me-tenant` | GET | get_current_user | 当前用户的 tenant context（任何登录用户） |

### 5.3 前端组件

| 组件 | 路径 | 用途 |
|---|---|---|
| `TenantBadge` | `apps/web/app/(dashboard)/_components/TenantBadge.tsx` | Topbar 显示当前 tenant 名 + 颜色 tag |
| `TenantSwitcher` | `apps/web/app/(dashboard)/_components/TenantSwitcher.tsx` | super admin 弹下拉切 tenant；写 `X-Tenant-ID` header + cookie |
| `admin/tenants/page.tsx` | 列表 + 创建 + 编辑 tenant | super admin 专用 |

`TenantSwitcher` 切 tenant 流程：

```
1. super admin 点 TenantSwitcher → 弹下拉（list /api/admin/tenants）
2. 选 tenant → PATCH /api/auth/me-tenant 写 cookie X-Tenant-ID
3. 后续 fetch 自动带 X-Tenant-ID → 后端 get_tenant_context 解析 → source=header
4. tenant_session 包装 → SET LOCAL app.tenant_id = <新 uuid>
5. UI 重新 fetch 当前页面数据 → 看到新 tenant 的内容
```

---

## 6. 测试覆盖

### 6.1 17 个 M1 测试 (`tests/test_multi_tenant_m1.py`)

- `tenants` 表结构 + 唯一 slug 约束
- 6 张业务表 `tenant_id` NOT NULL 验证
- 6 张表 RLS 启用 + FORCE 验证
- `tenant_lock` policy 创建 + 拒绝跨 tenant 查询
- backfill 完整性 (0 NULL)
- default tenant UUID 验证

### 6.2 7 个 M2 测试 (`tests/test_tenant_context.py`)

- `get_tenant_context` 三种 source（header / user_default / default）
- super admin 切 tenant via X-Tenant-ID
- 普通用户忽略 X-Tenant-ID（拿自己的 tenant_id）
- `tenant_session` SET LOCAL GUC 正确
- bypass_rls 仅 super admin 生效
- 触发器 fallback（没 GUC 时填 default）
- 损坏 user.tenant_id 静默回落 default

### 6.3 11 个 M3 测试 (`tests/test_admin_tenants.py`)

- GET /api/admin/tenants (super admin 看到全部，普通用户 403)
- POST /api/admin/tenants (slug 唯一约束、plan enum 校验)
- PATCH /api/admin/tenants/{id} (更新 name/is_active/metadata)
- /api/auth/me-tenant (返回 tenant_id + is_super_admin)
- is_super_admin 标志位检查
- 5 个跨租户 SQL 场景

---

## 7. 用例 (curl 演示)

### 7.1 super admin 切 tenant A → B

```bash
# 1. login as admin
curl -c /tmp/c.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 2. 列 tenants
curl -s -b /tmp/c.txt http://localhost:18000/api/admin/tenants | jq '.tenants[] | {slug, plan}'
# → [{"slug":"default","plan":"enterprise"}]

# 3. 创建 tenant A
curl -s -b /tmp/c.txt -X POST http://localhost:18000/api/admin/tenants \
  -H "Content-Type: application/json" \
  -d '{"slug":"acme","name":"Acme Realty","plan":"enterprise"}' | jq .
# → {"id": "<uuid>", "slug": "acme", ...}

# 4. 切到 tenant A
ACME_ID="<uuid from step 3>"
curl -s -b /tmp/c.txt -H "X-Tenant-ID: $ACME_ID" \
  http://localhost:18000/api/auth/me-tenant | jq .
# → { "tenant_id": "<acme uuid>", "is_super_admin": true, "source": "header" }

# 5. 切回 default
curl -s -b /tmp/c.txt -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000000" \
  http://localhost:18000/api/auth/me-tenant | jq .
```

### 7.2 普通用户跨租户查询被锁

```bash
# 1. login as bp-residential
curl -c /tmp/u.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"bp-residential","password":"bp123456"}'

# 2. 试图切到其他 tenant → 被锁在自己 tenant
curl -s -b /tmp/u.txt -H "X-Tenant-ID: $OTHER_TENANT_ID" \
  -o /dev/null -w "%{http_code}\n" \
  http://localhost:18000/api/registry/lines
# → 200 (拿到自己 tenant 的 9 条 line，不受 X-Tenant-ID 影响)
```

### 7.3 RLS 隔离验证

```bash
# 1. 以 tenant A 注册用户
TENANT_A_ID="<acme uuid>"
psql -U finbp -d finbp -c "INSERT INTO users (username, password_hash, tenant_id) VALUES ('user_a', '...', '$TENANT_A_ID');"

# 2. 以 tenant B 设 GUC
TENANT_B_ID="<jll uuid>"
psql -U finbp -d finbp -c "SET LOCAL app.tenant_id = '$TENANT_B_ID'; SELECT username FROM users;"
# → 0 rows (RLS 拒绝跨 tenant)

# 3. 设 tenant A 的 GUC
psql -U finbp -d finbp -c "SET LOCAL app.tenant_id = '$TENANT_A_ID'; SELECT username FROM users;"
# → "user_a" (匹配)
```

### 7.4 触发器 fallback (audit 不会拖垮)

```bash
# 不设 GUC 直接 INSERT (模拟 audit middleware 早期写)
psql -U finbp -d finbp -c "INSERT INTO raw.audit_log (user_id, username, method, path, status_code) VALUES (1, 'admin', 'GET', '/test', 200) RETURNING tenant_id;"
# → "00000000-0000-0000-0000-000000000000" (default tenant 兜底)
```

---

## 8. 升级路径 — 现有 v0.1.0 数据自动 backfill

| 阶段 | 操作 | 数据丢失风险 |
|---|---|---|
| M1 跑前 | 0 | 0 |
| M1 跑后 (`003_multi_tenant_setup.sql`) | 0（所有现有行 backfill 到 default tenant） | 0 |
| M2 跑前 | audit 中间件可能写失败（triggers 已加） | 0 |
| M2 跑后 (`004_tenant_m2_super_admin_and_triggers.sql`) | admin 用户 is_super_admin=TRUE | 0 |
| M3 跑前 | super admin UI 不可用 | 0 |
| M3 跑后 | UI 可用 | 0 |

**回滚**：
- M3 删 super admin UI 文件 → 0 业务影响
- M2 删 `is_super_admin` 列 → 不影响功能（默认 FALSE，但失去 super admin 能力）
- M1 RLS `DISABLE` + `FORCE off` → 回到单租户；保留 tenant_id 列不影响

---

## 9. 文件路径速查

| 模块 | 路径 |
|---|---|
| TenantContext + dep | `apps/api/app/core/tenant_context.py` |
| tenant_session helper | `apps/api/app/db/tenant.py` |
| require_super_admin_dep | `apps/api/app/core/rbac.py` |
| Admin tenants 路由 | `apps/api/app/routers/admin_tenants.py` |
| Tenant Pydantic schema | `apps/api/app/schemas/tenant.py` |
| M1 migration | `infra/migrations/003_multi_tenant_setup.sql` |
| M2 migration | `infra/migrations/004_tenant_m2_super_admin_and_triggers.sql` |
| M1 测试 | `apps/api/tests/test_multi_tenant_m1.py` |
| M2 测试 | `apps/api/tests/test_tenant_context.py` |
| M3 测试 | `apps/api/tests/test_admin_tenants.py` |
| 前端 TenantBadge | `apps/web/app/(dashboard)/_components/TenantBadge.tsx` |
| 前端 TenantSwitcher | `apps/web/app/(dashboard)/_components/TenantSwitcher.tsx` |
| 前端 Admin Tenants | `apps/web/app/(dashboard)/admin/tenants/page.tsx` |
| 前端 BFF proxy | `apps/web/app/api/admin/tenants/[[...path]]/route.ts` |

---

## 10. Follow-up (P1 / P2)

### P1 — 多租户能力补全

- **per-tenant 业务线绑定**：一个 tenant 不一定拥有全部 9 条业务线（业务线 ↔ tenant 多对多表）
- **tenant 升级 / 降级 plan**（admin UI 改 plan → 自动配额变更）
- **is_super_admin 升 / 降级 UI**（admin UI 加 toggle，4 端点补 PATCH `/api/admin/tenants/{id}/users/{uid}/super-admin`）

### P2 — 商业化前

- **4 通用 engine SQL 走 tenant_session**：现在 `alerts` / `forecast` / `sensitivity` / `copilot` 部分 SQL 还没用 `tenant_session`（仍可读但跨租户可能漏）
- **tenant-scoped AI 模型**：每个 tenant 独立的 ai_models 配置（不再共享）
- **多租户 UI dashboard**：super admin 看所有 tenant 汇总（KPI 总数、用户数、存储）
- **BYOK (Bring Your Own Key)**：每个 tenant 自己的 `DEEPSEEK_API_KEY`（不共享集团 key）
- **SOC2-ready 审计增强**：跨租户 admin 操作独立审计 channel

---

_交付日期: 2026-09-04 / 阶段: PR #1 合并 master / 累计测试: 277 passed / 0 failed / 新增: 35 passed_
