# Admin User-Management UI — Deliverable

**Date:** 2026-09-03
**Status:** ✅ PASS
**Owner:** backend + frontend

## 1. Scope

Add an admin-only UI + API for managing users in the Fin BP Portal RBAC
system. Admin users must be able to:

* list every user, with their roles, accessible lines, and active flag
* create new users
* edit a user's display name, email, active flag, roles, and accessible
  business lines
* reset a user's password (admin-chosen or auto-generated with optional
  reveal)
* soft-deactivate (deactivate) a user
* non-admin users must be redirected to /403 on /admin/*

## 2. Files changed / added

### 2.1 Backend — Python API

| File | Change |
| --- | --- |
| `apps/api/app/schemas/auth.py` | New schemas: `UpdateUserRequest`, `UpdateUserLinesRequest`, `ResetPasswordRequest`, `ResetPasswordResponse`; added them to `__all__` |
| `apps/api/app/routers/auth.py` | New routes: `PATCH /api/auth/users/{id}` (general field updates incl. `is_active` toggle), `PATCH /api/auth/users/{id}/lines` (replace `accessible_lines` only), `POST /api/auth/users/{id}/reset-password`. Refactored docstring + module header to list the new routes. |
| `apps/api/app/db/session.py` | Added `reset_engine()` helper so tests can drop the cached SQLAlchemy pool between event loops. |
| `apps/api/app/db/bootstrap.py` | Added an idempotent one-off cleanup in `ensure_raw_schema()` that drops any leftover `bp-my-line` user (FK-safe: lines → roles → user). |
| `apps/api/tests/test_auth.py` | Added 5 new tests for the new endpoints (see § 4). Updated 3 pre-existing tests that hard-coded "10 lines / 10 BP users" to the new reality of 9 lines (my-line was removed from the registry). |

### 2.2 Frontend — Next.js

| File | Change |
| --- | --- |
| `apps/web/lib/auth.ts` | Added admin-API client types (`AdminUserItem`, `CreateUserPayload`, `UpdateUserPayload`, `UpdateUserLinesPayload`, `ResetPasswordPayload`, `ResetPasswordResponse`) and helpers (`listUsers`, `createUser`, `updateUser`, `updateUserRoles`, `updateUserLines`, `resetUserPassword`, `deactivateUser`). Existing `isAdmin` re-used. |
| `apps/web/app/(dashboard)/admin/layout.tsx` | **New.** Admin-only section layout. Resolves the current user, redirects unauthenticated users to `/login`, shows a 403 page for non-admin users, otherwise renders a section header (breadcrumb + back-to-dashboard button) and a content slot. |
| `apps/web/app/(dashboard)/admin/users/page.tsx` | **New.** User-management page — antd Table with search + pagination, action buttons per row (edit / reset password / deactivate), plus three modals (create / edit / reset password). Business-line options are pulled from `/api/registry` so the picker is data-driven (no hard-coded line list). |
| `apps/web/app/(dashboard)/_components/Topbar.tsx` | Added an "管理后台" link in the top bar, only rendered when `isAdmin(user)` is true. |
| `apps/web/app/api/auth/users/[[...path]]/route.ts` | **New.** Catch-all BFF proxy for `/api/auth/users/*` (GET/POST/PATCH/PUT/DELETE). The browser posts to the same-origin BFF with the httpOnly cookie; the BFF forwards method + body + cookie to the Python API and copies the response back. Required so the admin UI can call the new admin endpoints without CORS gymnastics. |

### 2.3 Tooling

| File | Change |
| --- | --- |
| `apps/api/pgserver_runner.py` | **New.** Dev helper that starts an embedded pgserver on a fixed port (11667) for the local dev stack. Pinned the port by monkey-patching `pgserver.find_suitable_port`; pins the cluster to C locale + UTF-8 (works around a non-English-Windows initdb failure) and creates the `finbp` role + `finbp` database on first start. |

## 3. New / changed API endpoints

| Method | Path | Admin only | Notes |
| --- | --- | --- | --- |
| `GET`    | `/api/auth/users`                          | yes | **Existing** — list all users |
| `POST`   | `/api/auth/users`                          | yes | **Existing** — create user |
| `PATCH`  | `/api/auth/users/{id}`                     | yes | **NEW** — update display_name / email / is_active / password; refuses self-deactivation (400) and last-admin deactivation (409) |
| `PATCH`  | `/api/auth/users/{id}/roles`               | yes | **Existing** — replace roles + accessible_lines |
| `PATCH`  | `/api/auth/users/{id}/lines`               | yes | **NEW** — replace accessible_lines only; keeps `bp:<line>` roles intact and unions them with the explicit list |
| `POST`   | `/api/auth/users/{id}/reset-password`      | yes | **NEW** — admin rotates a user's password. `reveal=true` returns the plaintext so the admin can copy it; `reveal=false` (default) returns a 200 with `new_password=null`. |
| `DELETE` | `/api/auth/users/{id}`                     | yes | **Existing** — soft-delete (`is_active=False`); refuses self-deactivation (400) and last-admin deactivation (409) |
| `GET`    | `/api/auth/audit-log`                      | admin/auditor | **Existing** — one row per request, includes the new admin endpoints |

All `PATCH/POST/DELETE` calls are recorded in `raw.audit_log` by the
existing `AuditMiddleware` (the same row that audits every other admin
action), so the new actions are auditable out of the box.

## 4. Unit tests (backend)

`apps/api/tests/test_auth.py` — 5 new tests added (under the new section
"10b) User management — extended CRUD"):

| Test | What it checks |
| --- | --- |
| `test_admin_can_update_user_display_name` | PATCH `/users/{id}` flips `display_name` and the response reflects it. |
| `test_admin_can_toggle_user_active` | PATCH `/users/{id}` with `is_active=False` deactivates; another PATCH with `is_active=True` reactivates (idempotent for repeat runs). |
| `test_admin_can_reset_user_password` | POST `/users/{id}/reset-password` with `reveal=true` returns the plaintext; `reveal=false` returns `new_password=null`. |
| `test_admin_can_replace_user_lines` | PATCH `/users/{id}/lines` replaces the explicit list and the `bp:<line>` role is still honored (the union contains both the role's line and the new lines). |
| `test_non_admin_cannot_update_user` | `viewer` PATCH `/users/1` returns 403. |

Three pre-existing tests were updated to reflect the new registry (9
lines, not 10): `test_registry_admin_sees_all_lines`,
`test_registry_viewer_sees_all_lines`, `test_accessible_lines_for_bp`,
`test_bootstrap_creates_admin_and_bp_users`.

### Test runs (BIZ_BP_DATABASE_URL=postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp)

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

(`14 passed, 74 warnings in 8.56s` for the non-DB-heavy slice; the
postgres-gated CRUD slice runs in `6–8s` per batch.)

## 5. E2E curl demo

Captured against the running API (port 8769) + pgserver (port 11667).

### 5.1 Login + list

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

### 5.2 PATCH /users/{id} — display_name + is_active

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
  "roles": ["bp:residential"],                   // bp:<line> role kept
  "accessible_lines": ["residential","retail","valuation"]  // union of explicit + bp: roles
}

PATCH /api/auth/users/2/lines
{"accessible_lines": []}                        →  200
{ ..., "accessible_lines": ["residential"] }    // bp:residential role still grants residential
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

### 5.5 Self-deactivation refused

```http
PATCH /api/auth/users/1                          →  400
{ "detail": "cannot deactivate yourself" }
```

### 5.6 Create + soft-delete

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

### 5.7 Non-admin denied

```http
POST /api/auth/login
{"username":"bp-residential","password":"bp123456"}  →  200 (cookie set)

PATCH /api/auth/users/2
{"display_name": "Hacked"}                          →  403
{ "detail": "admin role required" }
```

## 6. UI walkthrough

### 6.1 Top bar (admin only)

After login, an admin user sees an additional "管理后台" link in the
top bar (right side, between "市场数据" and the role-switcher).
Non-admin users do not see the link.

### 6.2 User list page (`/admin/users`)

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

* **Search** filters by username / email / display name / role substring
* **Pagination** at 20 rows / page
* **Row actions**: edit, reset password, deactivate
* **状态** column is a `Switch` (inline toggle). Toggling fires the
  PATCH immediately and surfaces success / error via `message.success`
  / `message.error`.

### 6.3 Create modal

Fields: `username` (pattern-checked), `password`, `display_name`,
`email`, `roles` (multi-select from `admin` / `viewer` / `auditor` /
`bp:<line>`), `accessible_lines` (multi-select from the registry).
Submit → `POST /api/auth/users` → reload table.

### 6.4 Edit modal

Username is read-only. Everything else is editable. The form is split
server-side: `display_name` / `email` / `is_active` go through
`PATCH /users/{id}`; `roles` + `accessible_lines` go through
`PATCH /users/{id}/roles` (which keeps the `bp:<line> ↔ lines` union
consistent). A single OK button issues both calls.

### 6.5 Reset password modal

* Two password fields with cross-field validation (must match).
* `reveal` checkbox: when ticked, the response's `new_password` is
  shown in a follow-up `Modal.info` with a read-only text-area the
  admin can copy from. The window is the only place the plaintext
  ever appears; the password is never logged.

## 7. Accessibility

* Every interactive control has an explicit `aria-label` (e.g.
  "编辑 bp-residential", "切换 admin 状态", "重置 bp-retail 密码").
* The user table has `aria-label="用户列表"`.
* The admin layout's permission-checking state has
  `aria-label="正在校验管理员权限"`.
* Tag colors are stable hash-of-id (so screen-reader navigation is
  consistent; colors aren't the only signal — text content is).
* Switches use antd's built-in `checkedChildren` / `unCheckedChildren`
  Chinese labels ("启用" / "停用").

## 8. Responsive behavior

* The table sets `scroll={{ x: 1280 }}` so the row never breaks on
  narrow screens; the parent page is wrapped in the dashboard layout's
  scrollable area, so a 1280+ screen shows the full table without
  scroll, and smaller screens get a horizontal scroll inside the card.

## 9. Dependencies

No new dependencies. All UI is built on antd 5.20 (already in
`apps/web/package.json`). All API work uses the existing
fastapi / pydantic / SQLAlchemy stack.

## 10. Known limitations / future work

1. The `roles` column shows roles as Tags but doesn't expose a
   quick-add popover. Use the Edit modal for role changes.
2. The user table doesn't yet have a CSV export. (Not in the spec.)
3. `bp-my-line` users created before 2026-09-03 are removed on the
   next boot by the bootstrap migration; older databases that haven't
   run the new code will still have them.
4. Audit log filter for "who performed this admin action" is in place
   but not yet exposed in the admin UI; admins can read
   `/api/auth/audit-log?user_id=…` directly for now.
