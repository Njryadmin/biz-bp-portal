# Cockpit 交付物 — Fin BP Portal 驾驶舱 + 通用 UI 库

> Worker：Coder · T0.5（驾驶舱外壳 + 通用 UI）
> Date：2026-09-02
> Project root：`C:\Users\mozzi\.mavis\workspace\fin-bp-portal\`

## 1. 范围

T0 留下的是动态加载骨架：`apps/web/app/(dashboard)/layout.tsx`、
注册表 API、package 布局以及占位组件。本任务：

1. **串起驾驶舱外壳** —— 仪表盘布局现在 (a) 仅从 API 拉取注册表，
   (b) 左侧导航按业务线分组，(c) 通过 `usePathname` 高亮当前业务线，
   (d) 顶部条展示 `RoleSwitcher` 占位和用户菜单下拉占位。
2. **替换三个占位组件** —— 完整实现 `UniversalKpiCard`、`UniversalChart`
   和 `EmptyState`，外加新增的 `RoleSwitcher`。
3. **扩展注册表 API 契约** —— 使 `/api/registry/lines` 返回驾驶舱
   需要的 `display_name` 和 `indicators_count` 字段。
4. **记录 5 步"新增业务线"工作流** —— 文档位于 `docs/plugin-howto.md`。
5. **模拟一次 `test_line` 注册** 端到端验证契约。

## 2. 变更的文件

### apps/web（Next.js）

| 文件 | 操作 | 说明 |
|------|------|------|
| `apps/web/app/(dashboard)/layout.tsx` | 重写 | 服务端拉取，把菜单交给客户端 `SidebarMenu`，顶部条交给 `Topbar` |
| `apps/web/app/(dashboard)/_components/SidebarMenu.tsx` | 新增 | 客户端组件；用 `usePathname` 计算当前 key，按 `display_name` 排序，按业务线分组，分组头加粗当前线 |
| `apps/web/app/(dashboard)/_components/Topbar.tsx` | 新增 | 客户端组件；承载 `RoleSwitcher` + 用户菜单下拉（均为 UI 占位） |
| `apps/web/app/(dashboard)/dashboard/page.tsx` | 重写 | 业务线卡片网格：图标 + display_name + 描述 + 指标数量 + 版本；空状态用新的 `EmptyState` 配合文档 CTA |

`apps/web/app/page.tsx`、`apps/web/lib/registry.ts`、`apps/web/app/api/registry/route.ts`
在 T0 时已正确，**未做修改**。

### apps/api（FastAPI）

| 文件 | 操作 | 说明 |
|------|------|------|
| `apps/api/app/routers/registry.py` | 编辑 | `_summarize_line()` 将 `RegistryEntry` 投影为驾驶舱形态（新增 `display_name` 别名到 `name`，以及 `indicators_count = len(indicators)`）；`list_lines()` 现在返回该投影，而不是原始的 `BusinessLine` 模型转储 |
| `apps/api/tests/test_api.py` | 编辑 | 更新 `test_registry_endpoint` 以校验新形态（不再断言 `lines == []`，因为 T1/T2 已填充注册表）并新增 `test_registry_endpoint_shape_keys` |
| `apps/api/tests/test_registry.py` | 编辑 | 更新 `test_registry_yaml_loads` 并将 `test_load_registry_returns_empty_list_when_no_lines` 改名为 `test_load_registry_returns_list`，以适配已填充的注册表 |

### packages/types（TS）

| 文件 | 操作 | 说明 |
|------|------|------|
| `packages/types/src/index.ts` | 编辑 | 给 `BusinessLine` 新增可选字段 `display_name` 和 `indicators_count`（可选以保持向后兼容） |

### packages/ui（TS）

| 文件 | 操作 | 说明 |
|------|------|------|
| `packages/ui/src/UniversalKpiCard.tsx` | 重写 | 接受 `indicator: { id, name, unit?, format? }`；按 `format`（currency/number/percent/ratio）格式化数值，渲染内联 SVG 迷你图，以及 `delta`（环比）或 `trend`（字符串简写）并配上下/平箭头 + 颜色 |
| `packages/ui/src/UniversalChart.tsx` | 重写 | ECharts 5 工厂；按类型驱动 —— `line`、`bar`、`scatter`（`size` → symbolSize 映射）、`waterfall`（自动检测负值和 `isSubtract` 标记；一次计算占位与 delta 堆栈；可选总计柱）、`heatmap`（矩阵 + xCategories + yCategories + visualMap） |
| `packages/ui/src/EmptyState.tsx` | 重写 | 渲染 antd `Empty` 含 title + description + 可选文档 CTA 按钮，链接到 `docsHref`（仪表盘用它指向 `plugin-howto.md`） |
| `packages/ui/src/RoleSwitcher.tsx` | 新增 | 仅 UI 的占位下拉，覆盖任务要求的 4 个角色（Admin / BP-Residential / BP-Retail / BusinessHead）；不接鉴权 |
| `packages/ui/src/index.ts` | 编辑 | 导出新组件与类型 |

### docs

| 文件 | 操作 | 说明 |
|------|------|------|
| `docs/plugin-howto.md` | 新增 | 5 步指南（复制模板 → 编辑 manifest + indicators → 接入 api + 页面 → 在 `registry.yaml` 中注册 → 重启） + 自动发现图 + 注意事项清单 |
| `docs/cockpit-deliverable.md` | 新增 | 本文件 |

### business_lines（仅用于模拟注册，事后已清理）

| 文件 | 操作 | 说明 |
|------|------|------|
| `business_lines/registry.yaml` | 编辑（后回滚） | 临时追加 `- id: test_line` 以端到端验证注册表契约；已恢复到 T1+T2 状态（`residential`、`retail`） |
| `business_lines/test-line/` | 新建（后移走） | 临时复制 `_template` 并把 manifest 改为 `id: test_line`，以证明加载器工作；**已移出** `business_lines/` 到项目根的 `_test-line-staging/`（见 §6） |

## 3. 验证

### 3.1 TypeScript 类型检查

```text
$ cd apps/web && npm run typecheck
> @fin-bp/web@0.1.0 typecheck
> tsc --noEmit
（无输出 — 退出码 0）
```

### 3.2 API pytest

```text
$ cd apps/api && python -m pytest -q
..........                                                                [100%]
============================== warnings summary ===============================
..\..\..\..\..\AppData\Local\Programs\Python\Python312\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\mozzi\AppData\Local\Programs\Python\Python312\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.readthedocs.io/en/stable/how-to/capture-warnings.html
========================= 9 passed, 1 warning in 0.46s =========================
```

共采集 9 个测试，全部通过（1 个不相关的 starlette httpx 弃用警告）。

### 3.3 线上 API：`GET /api/registry/lines`（基线，仅 T1+T2）

```text
$ curl http://127.0.0.1:8768/api/registry/lines
{
  "version": "0.1.9720c1de",
  "lines": [
    { "id": "residential", "name": "...", "display_name": "...", "icon": "HomeOutlined",
      "indicators_count": 10, "nav": [...], "api_prefix": "/api/lines/residential", ... },
    { "id": "retail",      "name": "...", "display_name": "...", "icon": "ShopOutlined",
      "indicators_count": 8,  "nav": [...], "api_prefix": "/api/lines/retail",      ... }
  ]
}
```

每条业务线都包含契约字段：`id, name, display_name, icon, indicators_count, nav, api_prefix`（外加完整 BusinessLine payload）。`indicators_count` 来自该业务线 `indicators.yaml` 中实际定义的指标数量。

### 3.4 线上 API：模拟 `test_line` 注册

1. `cp -r business_lines/_template business_lines/test-line`（随后把 `*.example` 文件改为正式文件名）。
2. 编辑 `business_lines/test-line/manifest.yaml`，将 `id` 设为 `test_line`，`name: "Test Line (cockpit simulation)"`，`api_prefix: /api/lines/test_line`，`icon: ExperimentOutlined`。
3. 在 `business_lines/registry.yaml` 末尾追加 `- id: test_line, manifest: business_lines/test-line/manifest.yaml`。
4. 重启 API（`uvicorn --port 8768`）。
5. 启动日志：
   ```text
   2026-09-02T15:02:17 INFO [app.routers.registry] Mounted business line 'residential' (APIRouter) at /api/lines/residential
   2026-09-02T15:02:17 INFO [app.routers.registry] Mounted business line 'test_line' (APIRouter) at /api/lines/test_line
   ```
6. `GET /api/registry/lines` 现在返回 **3** 条业务线（residential、retail、test_line），`LINE_IDS: ["residential","retail","test_line"]`，其中 `test_line` 的摘要为：
   ```json
   {
     "id": "test_line",
     "name": "Test Line (cockpit simulation)",
     "display_name": "Test Line (cockpit simulation)",
     "icon": "ExperimentOutlined",
     "indicators_count": 2,
     "nav": [ { "path": "/test-line", "title": "Overview" } ],
     "api_prefix": "/api/lines/test_line"
   }
   ```
7. `GET /api/registry/lines/test_line` 返回完整 payload（line + 2 个指标 + 1 个图表），证明 loader 同时解析了每条业务线的 detail 端点。
8. `GET /api/lines/test_line/ping` 返回 `{"status":"ok","line":"change-me"}` —— 证明该业务线的 `api/router.py` 已通过动态加载器挂载到 `api_prefix` 下。

验证完成后，模拟数据已清理（见 §6）。

## 4. 关键设计决策

1. **Layout 不 import 任何业务线** —— 只 import `@fin-bp/types` 的 `BusinessLine` 和 API 响应。grep 验证：仅出现字面量 `business_lines/registry.yaml` 字符串，且仅在空状态提示的用户可见帮助文本里。
2. **客户端/服务端分离** —— 布局保持为服务端组件（拉取数据 + 渲染外壳），菜单和顶部条拆为 `SidebarMenu.tsx` / `Topbar.tsx` 客户端组件。这是需要 `usePathname` / `useState` 的最小表面。
3. **侧边栏按业务线分组** —— 扁平菜单让"当前业务线高亮"显得武断。侧边栏按业务线分组，分组标签中当前业务线加粗并在其名字旁带一个小三角。`Overview` 项置顶，在精确匹配时高亮。
4. **按 `display_name`（zh-CN 感知）排序** —— 使用
   `localeCompare("zh-Hans-CN", { sensitivity: "base" })`，使中文业务线名称按自然顺序排列。
5. **UniversalKpiCard** —— 接收规格中新的 prop 形态
   （`indicator: { id, name, unit, format }` + `value` + 可选 `delta`、`trend`、`sparkline`）。sparkline 用内联 SVG，组件保持零额外依赖。趋势箭头 + 颜色沿用 antd `<Statistic />` 的惯例。
6. **UniversalChart** —— 基于 ECharts 5 的类型驱动工厂。每种类型接受松散的 `data` 形态（扁平 `points` 数组或 `{ categories, values, ... }` 对象），并提供小型 `options` 转义口，通过 `options.echartsOverrides` 直接覆盖 ECharts 配置。waterfall 标准化器自动检测扣减柱（负值或 `isSubtract: true` 标记），一次计算出占位 + delta 堆栈。
7. **EmptyState** —— antd `Empty` 加一个 `docsHref` prop，提供时渲染一个主按钮链接到文档。仪表盘页面用它指向 `docs/plugin-howto.md`。
8. **RoleSwitcher** —— 仅 UI 占位，不接鉴权。暴露 `value` / `defaultRole` / `onChange`，方便未来真实鉴权方直接对接，不需改动调用方。
9. **注册表响应形态** —— `_summarize_line` 把每个 `RegistryEntry` 投影为 `{id, name, display_name, version, ..., icon, nav, api_prefix, ..., indicators_count}`。`display_name` 作为稳定、i18n 友好的扩展点（目前等价于 `name`）。完整 BusinessLine payload 仍然可以从 `/api/registry/lines/{line_id}` 获取，供详情页使用。
10. **`apps/web/lib/registry.ts` 和 `apps/web/app/api/registry/route.ts`**
    保持不变 —— 仍是规范的客户端拉取辅助与同源代理，新布局通过服务端 fetch 路径对 API 基础地址使用它们。

## 5. 假设

1. **角色名包含 "residential" / "retail"** —— 任务明确要求
   `Admin / BP-Residential / BP-Retail / BusinessHead`。约束"通用组件
   不耦合任何业务线特有概念"被解读为"不耦合特定业务线的数据、
   schema 或标识"，角色标签并不违反该约束。这两个字符串仅出现在
   角色名字面量中（不在任何业务线注册表查找、manifest 或页面里）。
   RoleSwitcher 是 UI 占位；真实鉴权方将替换标签。
2. **T1 + T2 worker 的业务线（`residential`、`retail`）在我工作期间存在** —— 这意味着 T0 那些假定 `lines == []` 的测试必须更新。我保留了
   "registry.yaml 是一个带 `lines` 键的 dict" 这一断言，只删除了
   `lines == []` 假设。测试现在校验的是**契约**（每条业务线摘要
   都具备驾驶舱所需字段），而不是注册表的**空状态**。
3. **`usePathname` 通过分组标签加粗实现高亮** —— antd Menu 没有
   "高亮分组标签" 一等 API，所以当前业务线在分组头处以加粗字体
   + 三角箭头表示。匹配的子项仍按 antd 的标准选中样式展示。
4. **Sparkline 使用内联 SVG 而非 ECharts** —— 规格中将 sparkline
   描述为"迷你"图，28 像素高的 polyline 是最轻的实现方式。在该像素
   预算下组件零依赖；如果将来需要更高保真度，可单独抽出
   `<UniversalSparkline>` 而不动此文件。
5. **无生产级 CORS / 鉴权** —— 按任务要求不属于本次范围。
   `CORSMiddleware` 在开发模式下放行所有来源，`apps/web/app/api/registry/route.ts`
   中的同源代理路由是推荐的开发路径。
6. **T1/T2 业务线在 UI 中渲染的中文正确** —— API 和 Next.js
   布局都将字符串视为不透明 UTF-8。PowerShell 测试管道出现乱码
   是因为本地 PowerShell 会话默认使用 ANSI 代码页，但 API 本身
   返回的是合法 UTF-8（注册表文件 loader 使用 `encoding="utf-8"` 打开）。

## 6. 阻塞 / 限制

1. **硬性安全策略禁止 `Remove-Item -Recurse -Force` 以及
   `mavis-trash.cmd` 包装器用于清理 test-line。** 按系统策略，本
   agent 的 bash 工具不能执行任何删除命令。因此临时目录
   `business_lines/test-line/` 被移出 `business_lines/` 到项目根
   的 `_test-line-staging/`，注册表 loader 不再扫描到它
   （通过 `GET /api/registry/lines` 在移动后只返回 `residential`
   和 `retail` 验证）。目录仍在磁盘上，可手动移到回收站。
2. **工作目录是 reparse point** —— `C:\Users\mozzi\.mavis\workspace\`
   重定向到 `C:\Users\mozzi\.minimax\workspace\`。API 日志打印
   解析后的根路径（`C:\Users\mozzi\.minimax\workspace\...`）。
   拒绝 reparse 路径的工具需要传入解析后的路径。
3. **AG Grid CSS** —— `packages/ui/src/UniversalAgGrid.tsx` 已
   import quartz 主题 CSS，因此在任何使用处都能工作，但
   `apps/web` 中目前还没有任何页面 import 它。T1/T2 可能会新增
   引用该包的 grid 页面。
4. **worker 主机未安装 docker** —— infra 的 `docker-compose.yml`
   在 T0 已做结构校验，但此处无法启动。API 走的是进程内
   TestClient + 真实 uvicorn 进程；数仓后端（Postgres /
   ClickHouse / MinIO）未被实际跑过。
5. **未做 Next.js 外壳的浏览器自动化测试。** 我做了项目的
   typecheck 并端到端跑了 API，但没有启动 `next dev` 在真实
   浏览器中驱动仪表盘。服务端组件比较简单（一次 fetch + JSX），
   客户端组件易于单元测试；后续可加 Playwright 测试覆盖侧边栏
   高亮行为。

## 7. plugin how-to 的步骤数

`docs/plugin-howto.md` 恰好有 **5 个编号步骤**（0 是总览图，1-5
是操作步骤），符合"≤ 5 步"的验收标准。
