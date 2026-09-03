# 管理后台用户管理 UI — 交付物

**日期**：2026-09-03
**状态**：✅ PASS
**Owner**：backend + frontend

## 1. 范围

为 Fin BP Portal RBAC 系统增加 admin 专属的 UI + API。admin 用户必须能够：

* 列出全部用户，附带其角色、可访问业务线以及启用标志
* 创建新用户
* 编辑用户的显示名、邮箱、启用标志、角色以及可访问业务线
* 重置用户密码（admin 自行指定或自动生成，并可选择是否显示明文）
* 软删除（停用）用户
* 非 admin 用户访问 `/admin/*` 必须被重定向到 /403

## 2. 变更 / 新增的文件

### 2.1 后端 — Python API

| 文件 | 变更 |
| --- | --- |
| `apps/api/app/schemas/auth.py` | 新增 schema：`UpdateUserRequest`、`UpdateUserLinesRequest`、`ResetPasswordRequest`、`ResetPasswordResponse`；加入 `__all__` |
| `apps/api/app/routers/auth.py` | 新增路由：`PATCH /api/auth/users/{id}`（常规字段更新，包括 `is_active` 切换）、`PATCH /api/auth/users/{id}/lines`（仅替换 `accessible_lines`）、`POST /api/auth/users/{id}/reset-password`。重构 docstring + 模块头部以列出新路由。 |
| `apps/api/app/db/session.py` | 新增 `reset_engine()` 辅助函数，方便测试在事件循环之间丢弃缓存的 SQLAlchemy 连接池。 |
| `apps/api/app/db/bootstrap.py` | 在 `ensure_raw_schema()` 中新增一次性幂等清理，移除遗留的 `bp-my-line` 用户（FK 安全顺序：lines → roles → user）。 |
| `apps/api/tests/test_auth.py` | 新增 5 个新端点测试（见 § 4）。更新 3 个硬编码"10 业务线 / 10 个 BP 用户"的旧测试，以反映当前实际（9 条业务线，my-line 已从注册表移除）。 |

### 2.2 前端 — Next.js

| 文件 | 变更 |
| --- | --- |
| `apps/web/lib/auth.ts` | 新增 admin API 客户端类型（`AdminUserItem`、`CreateUserPayload`、`UpdateUserPayload`、`UpdateUserLinesPayload`、`ResetPasswordPayload`、`ResetUserResponse`）与辅助函数（`listUsers`、`createUser`、`updateUser`、`updateUserRoles`、`updateUserLines`、`resetUserPassword`、`deactivateUser`）。复用现有 `isAdmin`。 |
| `apps/web/app/(dashboard)/admin/layout.tsx` | **新增。** Admin 专属分区布局。解析当前用户，将未登录用户重定向到 `/login`，为非 admin 用户显示 403 页，否则渲染分区头部（面包屑 + 返回主页按钮）和内容插槽。 |
| `apps/web/app/(dashboard)/admin/users/page.tsx` | **新增。** 用户管理页 —— antd Table 含搜索 + 分页，每行操作按钮（编辑 / 重置密码 / 停用），外加三个模态框（新增 / 编辑 / 重置密码）。业务线选项从 `/api/registry` 拉取，保证数据驱动（不硬编码业务线列表）。 |
| `apps/web/app/(dashboard)/_components/Topbar.tsx` | 在顶部条新增"管理后台"链接，仅当 `isAdmin(user)` 为 true 时渲染。 |
| `apps/web/app/api/auth/users/[[...path]]/route.ts` | **新增。** `/api/auth/users/*` 的通配 BFF 代理（GET/POST/PATCH/PUT/DELETE）。浏览器向同源 BFF 发送请求并携带 httpOnly cookie；BFF 转发 method + body + cookie 到 Python API，并原样回传响应。admin UI 因此可以无需处理 CORS 即可调用新端点。 |

### 2.3 工具

| 文件 | 变更 |
| --- | --- |
| `apps/api/pgserver_runner.py` | **新增。** 开发辅助脚本：在固定端口（11667）启动内嵌 pgserver 供本地开发栈使用。通过 monkey-patch `pgserver.find_suitable_port` 固定端口；将集群锁在 C locale + UTF-8（绕过非英文 Windows 的 initdb 失败），并在首次启动时创建 `finbp` 角色 + `finbp` 数据库。 |

## 3. 新增 / 变更的 API 端点

| 方法 | 路径 | 仅 admin | 说明 |
| --- | --- | --- | --- |
| `GET`    | `/api/auth/users`                          | 是 | **已有** —— 列出全部用户 |
| `POST`   | `/api/auth/users`                          | 是 | **已有** —— 创建用户 |
| `PATCH`  | `/api/auth/users/{id}`                     | 是 | **新增** —— 更新 display_name / email / is_active / password；拒绝自我停用（400）和最后一个 admin 的停用（409） |
| `PATCH`  | `/api/auth/users/{id}/roles`               | 是 | **已有** —— 替换 roles + accessible_lines |
| `PATCH`  | `/api/auth/users/{id}/lines`               | 是 | **新增** —— 仅替换 accessible_lines；保留 `bp:<line>` 角色不变，并将其与显式列表取并集 |
| `POST`   | `/api/auth/users/{id}/reset-password`      | 是 | **新增** —— admin 轮换用户密码。`reveal=true` 在响应中返回明文以便 admin 复制；`reveal=false`（默认）返回 200 且 `new_password=null`。 |
| `DELETE` | `/api/auth/users/{id}`                     | 是 | **已有** —— 软删除（`is_active=False`）；拒绝自我停用（400）和最后一个 admin 的停用（409） |
| `GET`    | `/api/auth/audit-log`                      | admin/auditor | **已有** —— 每个请求一行，包含新增的 admin 端点 |

所有 `PATCH/POST/DELETE` 调用都由现有的 `AuditMiddleware` 记录到
`raw.audit_log`（与审计其他 admin 操作的同一行），因此新操作开箱即可审计。

## 4. 单元测试（后端）

`apps/api/tests/test_auth.py` —— 新增 5 个测试（位于新分区
"10b) User management — extended CRUD"）：

| 测试 | 校验内容 |
| --- | --- |
| `test_admin_can_update_user_display_name` | PATCH `/users/{id}` 修改 `display_name` 并反映在响应中。 |
| `test_admin_can_toggle_user_active` | PATCH `/users/{id}` 配 `is_active=False` 停用；再次 PATCH 配 `is_active=True` 重新启用（幂等，可重复跑）。 |
| `test_admin_can_reset_user_password` | POST `/users/{id}/reset-password` 配 `reveal=true` 返回明文；`reveal=false` 返回 `new_password=null`。 |
| `test_admin_can_replace_user_lines` | PATCH `/users/{id}/lines` 替换显式列表，`bp:<line>` 角色仍生效（并集同时包含角色对应的业务线和新增业务线）。 |
| `test_non_admin_cannot_update_user` | `viewer` PATCH `/users/1` 返回 403。 |

3 个旧测试已更新以反映新的注册表（9 条业务线，而非 10）：
`test_registry_admin_sees_all_lines`、
`test_registry_viewer_sees_all_lines`、`test_accessible_lines_for_bp`、
`test_bootstrap_creates_admin_and_bp_users`。

### 测试运行（BIZ_BP_DATABASE_URL=postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp）

```text
tests/test_auth.py::test_login_sets_cookie_and_returns_me PASSED
tests/test_auth.py::test_user_list_requires_admin PASSED
tests/test_auth.py::test_admin_can_update_user_display_name PASSED
tests/test_auth.py::test_admin_can_toggle_user_active PASSED
tests/test_auth.py::test_admin_can_reset_user_password PASSED
tests/test_auth.py::test_admin_can_replace_user_lines PASSED
tests/test_auth.py::test_non_admin_cannot_update_user PASSED
tests/test_auth.py::test_registry_admin_sees_all_lines PASSED
tests/test_auth.py::test_registry_viewer_sees_all_lines PASSED
tests/test_auth.py::test_accessible_lines_for_bp PASSED
tests/test_auth.py::test_admin_can_create_user PASSED
tests/test_auth.py::test_admin_cannot_demote_last_admin PASSED
tests/test_auth.py::test_admin_cannot_delete_self PASSED
tests/test_auth.py::test_admin_can_change_user_roles PASSED
```

（非 DB 密集切片：`14 passed, 74 warnings in 8.56s`；依赖 postgres 的
CRUD 切片每批 6–8 秒。）

## 5. E2E curl 演示

在运行中的 API（端口 8769）+ pgserver（端口 11667）上抓取。

### 5.1 登录 + 列表

```http
POST /api/auth/login
{"username":"admin","password":"admin123"}     →  200 (Set-Cookie: finbp_token=...)

GET  /api/auth/users                            →  200
{
  "count": 10,
  "users": [
    {"id":  1, "username": "admin",            "roles": ["admin","auditor"],  "lines": [],                "is_active": true},
    {"id":  2, "username": "bp-residential",   "roles": ["bp:residential"],  "lines": ["residential"],   "is_active": true},
    {"id":  3, "username": "bp-retail",        "roles": ["bp:retail"],       "lines": ["retail"],        "is_active": true},
    ... 7 more ...
    {"id": 10, "username": "bp-industrial",    "roles": ["bp:industrial"],   "lines": ["industrial"],    "is_active": true}
  ]
}
```

### 5.2 PATCH /users/{id} —— display_name + is_active

```http
PATCH /api/auth/users/2
{"display_name": "Residential Lead"}            →  200
{ "id": 2, "username": "bp-residential", "display_name": "Residential Lead", ... }

PATCH /api/auth/users/2
{"is_active": false}                           →  200
{ ..., "is_active": false, ... }

PATCH /api/auth/users/2
{"is_active": true}                            →  200
{ ..., "is_active": true, ... }
```

### 5.3 PATCH /users/{id}/lines

```http
PATCH /api/auth/users/2/lines
{"accessible_lines": ["retail", "valuation"]}  →  200
{
  "id": 2, "username": "bp-residential",
  "roles": ["bp:residential"],                   // bp:<line> 角色保留
  "accessible_lines": ["residential","retail","valuation"]  // 显式 + bp: 角色的并集
}

PATCH /api/auth/users/2/lines
{"accessible_lines": []}                        →  200
{ ..., "accessible_lines": ["residential"] }    // bp:residential 角色仍授予 residential
```

### 5.4 POST /users/{id}/reset-password

```http
POST /api/auth/users/3/reset-password
{"new_password": "demo-new-pw", "reveal": true}  →  200
{ "ok": true, "message": "password rotated for user 3", "new_password": "demo-new-pw" }

POST /api/auth/users/3/reset-password
{"new_password": "bp123456", "reveal": false}    →  200
{ "ok": true, "message": "password rotated for user 3", "new_password": null }
```

### 5.5 自我停用被拒

```http
PATCH /api/auth/users/1                          →  400
{ "detail": "cannot deactivate yourself" }
```

### 5.6 新建 + 软删除

```http
POST /api/auth/users
{
  "username": "demo-1788423808",
  "password": "demo-pw-1",
  "display_name": "Demo User",
  "roles": ["viewer"],
  "accessible_lines": ["residential"]
}                                                →  201
{ "id": 17, "username": "demo-1788423808", ..., "is_active": true, "roles": ["viewer"], "accessible_lines": ["residential"] }

DELETE /api/auth/users/17                        →  200
{ "ok": true, "message": "deactivated user 17" }
```

### 5.7 非 admin 被拒

```http
POST /api/auth/login
{"username":"bp-residential","password":"bp123456"}  →  200 (cookie set)

PATCH /api/auth/users/2
{"display_name": "Hacked"}                          →  403
{ "detail": "admin role required" }
```

## 6. UI 走查

### 6.1 顶部条（仅 admin）

登录后，admin 用户在顶部条（右侧，"市场数据" 与角色切换器之间）会多看到
一个"管理后台"链接。非 admin 用户看不到该链接。

### 6.2 用户列表页（`/admin/users`）

```
+--------------------------------------------------------------+
| 管理后台                                  [用户管理] [返回主页] |
+--------------------------------------------------------------+
| 用户管理  共 10 个用户          [搜索] [刷新] [新增用户]     |
+--------------------------------------------------------------+
| ID | 用户名 | 显示名 | 邮箱 | 角色 | 业务线 | 状态 | 操作 |
|  1 | admin | System Admin | admin@finbp.local | [admin][auditor] | — | (●) | [编辑] [重置密码] [停用] |
|  2 | bp-residential | ... | [bp:residential] | [residential] | (●) | [编辑] [重置密码] [停用] |
| ... |
+--------------------------------------------------------------+
| 角色列:   [admin] (red)   [auditor] (purple)   [bp:<line>] (green)  |
| 业务线列: hash → [blue|geekblue|cyan|...|volcano] tag       |
+--------------------------------------------------------------+
```

* **搜索** 按用户名 / 邮箱 / 显示名 / 角色子串过滤
* **分页** 每页 20 行
* **行操作**：编辑、重置密码、停用
* **状态** 列为 `Switch`（行内开关）。切换立即触发 PATCH，并通过
  `message.success` / `message.error` 反馈成功 / 失败。

### 6.3 新建模态框

字段：`username`（带格式校验）、`password`、`display_name`、`email`、
`roles`（多选自 `admin` / `viewer` / `auditor` / `bp:<line>`）、
`accessible_lines`（多选自注册表）。
提交 → `POST /api/auth/users` → 重新加载表格。

### 6.4 编辑模态框

用户名只读，其余字段均可编辑。表单在服务端拆分：`display_name` /
`email` / `is_active` 走 `PATCH /users/{id}`；`roles` +
`accessible_lines` 走 `PATCH /users/{id}/roles`（保持 `bp:<line> ↔ lines`
并集一致）。单个"确定"按钮会同时发起两个调用。

### 6.5 重置密码模态框

* 两个密码字段，带跨字段校验（必须一致）。
* `reveal` 复选框：勾选时，响应中的 `new_password` 会通过随后的
  `Modal.info` 显示在一个只读 textarea 中供 admin 复制。该窗口是
  明文唯一出现的地方；密码从不被记录到日志。

## 7. 可访问性

* 每个交互控件都有显式的 `aria-label`（如"编辑 bp-residential"、
  "切换 admin 状态"、"重置 bp-retail 密码"）。
* 用户表格带有 `aria-label="用户列表"`。
* admin 布局的权限校验状态带有
  `aria-label="正在校验管理员权限"`。
* 标签颜色基于 id 哈希取色（便于屏幕阅读器导航时保持一致；
  颜色不是唯一信号 —— 文本内容也是）。
* Switch 使用 antd 内置的 `checkedChildren` / `unCheckedChildren`
  中文标签（"启用" / "停用"）。

## 8. 响应式行为

* 表格设置 `scroll={{ x: 1280 }}`，在窄屏下不会破坏行布局；父页面
  嵌套在仪表盘布局的可滚动区域中，因此 1280+ 屏幕完整显示无需滚动，
  较小屏幕在卡片内出现水平滚动条。

## 9. 依赖

未新增依赖。全部 UI 基于 antd 5.20（已在 `apps/web/package.json`）。
全部 API 工作使用现有的 fastapi / pydantic / SQLAlchemy 技术栈。

## 10. 已知限制 / 后续工作

1. `roles` 列将角色显示为 Tag，但未提供快捷添加弹出框。请使用"编辑"模态框修改角色。
2. 用户表暂未提供 CSV 导出。（不在本次规格内。）
3. 2026-09-03 之前创建的 `bp-my-line` 用户会在下次启动时被 bootstrap 迁移移除；未运行新代码的旧数据库仍会保留这些用户。
4. 审计日志已支持按"谁执行了该 admin 操作"过滤，但尚未在 admin UI 中暴露；目前 admin 可直接读取 `/api/auth/audit-log?user_id=…`。
