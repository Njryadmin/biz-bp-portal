# apps/web — Next.js 14 前端

> 配套：[`MAINTENANCE.md`](../../MAINTENANCE.md) §1.1（端口 + URL）；[`apps/api/README.md`](../api/README.md)（API 行为）。

---

## 启动

```powershell
# 在仓库根
cd 'C:\Users\mozzi\.mavis\workspace\biz-bp-portal'
npm install                          # 一次
npm run web:dev                      # dev server
# → http://localhost:3000
```

| 命令 | 作用 |
|---|---|
| `npm run web:dev` | dev server (port 3000) |
| `npm run web:build` | 生产构建（产出 .next/） |
| `npm run web:start` | 跑生产构建 |
| `npm run web:lint` | ESLint |
| `npx tsc --noEmit` | TypeScript 类型检查（不开 tsc watcher） |

---

## 目录结构

```
apps/web/
├── app/
│   ├── layout.tsx                 ← 根布局：antd registry + ConfigProvider
│   ├── page.tsx                   ← / （重定向到 /dashboard 或 /login）
│   ├── (dashboard)/               ← 受保护页面（middleware 要求 cookie）
│   │   ├── layout.tsx             ← 渲染 Sidebar + Topbar
│   │   ├── _components/
│   │   │   ├── SidebarMenu.tsx    ← 左导航（按 accessible_lines 过滤）
│   │   │   ├── Topbar.tsx         ← 顶栏（用户 + 注销）
│   │   │   └── linePageConfig.ts  ← 业务线 page 配置
│   │   ├── dashboard/             ← 总览
│   │   ├── sensitivity/           ← 敏感性 Lab
│   │   ├── copilot/               ← AI 问答
│   │   ├── forecast/              ← 滚动预测
│   │   ├── alerts/                ← 告警中心
│   │   ├── scrapers/              ← 爬虫面板
│   │   ├── admin/
│   │   │   ├── users/             ← 用户管理（admin）
│   │   │   └── ai-models/         ← AI 模型管理（admin）
│   │   ├── [line]/                ← 业务线动态路由
│   │   │   ├── page.tsx           ← /<line>
│   │   │   └── [page]/page.tsx    ← /<line>/<page>
│   ├── api/                       ← BFF 代理
│   │   ├── auth/                  ← 登录 / 注销 / me
│   │   ├── registry/              ← 业务线清单
│   │   ├── lines/[[...path]]/     ← 业务线 catch-all
│   │   ├── sensitivity/
│   │   ├── forecast/
│   │   ├── alerts/
│   │   ├── copilot/
│   │   ├── scrapers/
│   │   ├── ai-models/[[...path]]/ ← AI 模型 catch-all
│   │   └── auth/users/[[...path]] ← 用户管理 catch-all
│   ├── login/                     ← 公共：登录页
│   ├── 403/                       ← 公共：403 页
│   └── 404/                       ← 公共：404 页（如果存在）
├── middleware.ts                  ← cookie 守卫
├── package.json
├── tsconfig.json
├── .eslintrc.json
├── next-env.d.ts
└── README.md                      ← 你正在读
```

---

## 关键技术决策

### 1. App Router（不是 Pages Router）

`app/` 目录 + `layout.tsx` 嵌套 + Server Components 默认。
BFF 用 `app/api/**/route.ts` 而不是 `pages/api/*`。

### 2. antd v5 + Next.js 14 的 SSR 模式

`apps/web/app/layout.tsx`：

```typescript
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { ConfigProvider } from "antd";

<AntdRegistry>
  <ConfigProvider theme={{ token: { colorPrimary: "#1677ff" } }}>
    {children}
  </ConfigProvider>
</AntdRegistry>
```

`AntdRegistry` 处理 antd 的 CSS-in-JS 注入到 `<head>`。
**任何 antd 页面必须加 `'use client'`**（参见 [`docs/cockpit-deliverable.md`](../../docs/cockpit-deliverable.md) 的失败 / 修复记录）。

### 3. BFF 代理（apps/web/app/api/**）

每个 BFF route.ts 必须：

```typescript
export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";

const upstream = await fetch(`${BASE}/api/...`, {
  method: request.method,
  headers: {
    cookie: request.headers.get("cookie") ?? "",  // ← 关键
    "content-type": request.headers.get("content-type") ?? "application/json",
  },
  body: ...,  // 仅非 GET/HEAD
  cache: "no-store",
  duplex: "half",  // undici 必需
});
```

完整模板见 [`docs/maintenance/extending.md`](../../docs/maintenance/extending.md) §3。

### 4. Cookie 守卫

`apps/web/middleware.ts:1` 在 edge runtime 跑：

```typescript
const COOKIE_NAME = process.env.BIZ_BP_COOKIE_NAME || "finbp_token";

export function middleware(request) {
  if (isPublicPath(pathname)) return NextResponse.next();
  const token = request.cookies.get(COOKIE_NAME)?.value;
  if (token) return NextResponse.next();
  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("from", pathname + search);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

**不**验证 JWT 签名（API 做这件事）。缺 cookie → redirect，伪造 cookie → API 401。

### 5. 动态业务线路由

`apps/web/app/(dashboard)/[line]/page.tsx` 与 `[line]/[page]/page.tsx`：
- 任何业务线 id 都通过这两个动态路由渲染
- 实际显示什么由 `linePageConfig.ts` 决定（用 manifest 的 `nav[]`）

`SidebarMenu` 通过 `accessibleLineIds` prop 过滤（来自 `getCurrentUser()`）。

---

## 公共组件（packages/ui）

`apps/web` 通过 `import { ... } from "@biz-bp/ui"` 引用：

| 组件 | 用途 |
|---|---|
| `UniversalKpiCard` | 单个 KPI 卡片（数值 + 同比 + sparkline） |
| `UniversalChart` | ECharts 包装（line / bar / pie / area） |
| `UniversalAgGrid` | ag-Grid 表格 |
| `EmptyState` | 空状态占位 |
| `RoleSwitcher` | 角色 tag 展示（read-only） |

---

## 共享类型（packages/types）

`import type { BusinessLine, Indicator } from "@biz-bp/types"`。

每加一个 Pydantic response 都要**同步**加到 `packages/types/src/index.ts`。
参见 [`packages/types/README.md`](../../packages/types/README.md)。

---

## 环境变量

| 变量 | 何时设 | 用途 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | 必需 | BFF 转发的目标 API 地址（dev: `http://localhost:8769`） |
| `BIZ_BP_COOKIE_NAME` | 可选 | 中间件读的 cookie 名（默认 `finbp_token`） |
| `NODE_ENV` | 自动 | dev / production |
| `NEXT_TELEMETRY_DISABLED` | 生产建议 | 关 Next 内置遥测 |

**Next.js 约定**：`NEXT_PUBLIC_*` 进前端 bundle，其它仅服务端可见。

---

## TypeScript 严格性

`tsconfig.json` 启用 `strict: true`。规则：

- ❌ `any`（用 `unknown` + 收窄）
- ❌ `// @ts-ignore`（用 `// @ts-expect-error <reason>`）
- ✅ 严格 null check
- ✅ `noImplicitAny`
- ✅ `strictFunctionTypes`

CI / 提交前必须 `npx tsc --noEmit` 通过。

---

## 常见操作

### 重新构建

```powershell
# 删 .next/ 强制重新构建
py -X utf8 -c "import shutil; shutil.rmtree(r'C:\...\apps\web\.next', ignore_errors=True)"
npm run web:dev
```

### 看 BFF 请求日志

BFF 在 Next.js console 打 `[Proxy] GET /api/... → 200`（如果加了 console.log）。
生产用 `docker compose logs -f web`。

### 调试 RSC / SSR 问题

参见 [`docs/cockpit-deliverable.md`](../../docs/cockpit-deliverable.md) 的失败模式总结。

### 加新页面

参见 [`docs/maintenance/extending.md`](../../docs/maintenance/extending.md)。

---

## 不要做的事

- ❌ 不要在 server component 里用 antd（`'use client'` 必须）
- ❌ 不要在 BFF 里做业务逻辑（解析 JWT / 查 DB）
- ❌ 不要在 BFF 里缓存响应
- ❌ 不要 `fetch` 到 `localhost:8769` / `:8000`（永远走 `/api/...` BFF）
- ❌ 不要修改 `apps/web/.next/`（构建产物）
- ❌ 不要在 BFF route.ts 漏 `export const dynamic = "force-dynamic"`
- ❌ 不要在 BFF route.ts 漏 `cookie` 头转发
- ❌ 不要 `import { ... } from "business_lines.X"`（业务线独立）
