// apps/web/lib/auth.ts
//
// RBAC 系统的浏览器端辅助函数。纯函数 + 精简的 fetch 包装；
// 不依赖 React，可在任意组件中复用（包括服务端组件，通过
// ``accessToken`` 辅助函数支持 SSR）。

import type {
  UpdateUserV2RolesPayload,
  UserRoleBinding,
  UserV2RolesResponse,
  V2CurrentUser,
  V2Role,
  V2Scope,
} from "@biz-bp/types";

export type RoleName = string;

export interface CurrentUser {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  is_active: boolean;
  roles: RoleName[];
  accessible_lines: string[];
  /**
   * M3 (2026-09-04): super admin flag. NOT exposed in the v1
   * /api/auth/me response (the v1 contract is locked) — instead
   * the frontend learns it from /api/auth/me-tenant which adds the
   * field. The layout reads the me-tenant response once and merges
   * `is_super_admin` into the local CurrentUser object so existing
   * isAdmin(...) call sites can keep using a single `user` object.
   *
   * Optional on the type because legacy /me responses (and
   * synthetic users created in tests) don't carry it.
   */
  is_super_admin?: boolean;
}

export interface AccessibleLines {
  count: number;
  lines: string[];
  all_lines: string[];
}

// ---------------------------------------------------------------------------
// 管理后台用户管理请求体（与 apps/api/app/schemas/auth.py 对应）
// ---------------------------------------------------------------------------

export interface AdminUserItem {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  is_active: boolean;
  roles: string[];
  accessible_lines: string[];
  created_at: string;
  /**
   * v2 RBAC bindings (role + scope + line_id). Additive — v1 clients
   * that only read `roles` / `accessible_lines` keep working. Populated
   * by GET /api/auth/users (C1 added it to UserListItem). Optional so
   * older API responses don't break the type.
   */
  v2_bindings?: UserRoleBinding[];
}

export interface AdminUserListResponse {
  count: number;
  users: AdminUserItem[];
}

export interface CreateUserPayload {
  username: string;
  password: string;
  display_name?: string;
  email?: string;
  roles: string[];
  accessible_lines?: string[];
}

export interface UpdateUserPayload {
  display_name?: string;
  email?: string;
  is_active?: boolean;
  password?: string;
  // 设为 true 以显式将用户的 email 字段置为 NULL。
  // Pydantic 的 EmailStr 不接受空字符串，因此使用单独的标志位。
  // 后端将 clear_email=true 解释为"将 email 置为 NULL"，
  // 其优先级高于 email 字段中的任何值。
  clear_email?: boolean;
}

export interface UpdateUserLinesPayload {
  accessible_lines: string[];
}

export interface ResetPasswordPayload {
  new_password: string;
  reveal?: boolean;
}

export interface ResetPasswordResponse {
  ok: boolean;
  message: string;
  new_password?: string | null;
}

async function readJson(res: Response) {
  if (res.status === 204) return null;
  return res.json().catch(() => null);
}

export async function getCurrentUser(): Promise<CurrentUser | null> {
  const res = await fetch("/api/auth/me", {
    cache: "no-store",
    credentials: "include",
  });
  if (!res.ok) return null;
  const data = (await readJson(res)) as CurrentUser | null;
  return data;
}

// M3 (2026-09-04): Tenant self-view. Re-exported from
// lib/tenants.ts so existing callers can keep importing from
// lib/auth.ts (a single canonical location for "who am I" helpers).
export { getMyTenant as getCurrentUserTenant } from "./tenants";

// ---------------------------------------------------------------------------
// v2 CurrentUser (E, 2026-09-04)
//
// Used by PerspectiveSwitcher + dashboard pages. Always returns the v2
// shape including `bindings` and `active_view`. Returns null if the
// user is unauthenticated (the BFF will have given a 401).
// ---------------------------------------------------------------------------

export async function getCurrentUserV2(): Promise<V2CurrentUser | null> {
  const res = await fetch("/api/auth/me-v2", {
    cache: "no-store",
    credentials: "include",
  });
  if (!res.ok) return null;
  const data = (await readJson(res)) as V2CurrentUser | null;
  return data;
}

export async function login(
  username: string,
  password: string,
): Promise<CurrentUser> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const data = (await readJson(res)) as { detail?: string } | null;
    throw new Error(data?.detail || `login failed (HTTP ${res.status})`);
  }
  return (await readJson(res)) as CurrentUser;
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "include",
  });
}

export async function getAccessibleLines(): Promise<AccessibleLines> {
  const res = await fetch("/api/auth/accessible-lines", {
    cache: "no-store",
    credentials: "include",
  });
  if (!res.ok) {
    return { count: 0, lines: [], all_lines: [] };
  }
  return ((await readJson(res)) as AccessibleLines) ?? {
    count: 0,
    lines: [],
    all_lines: [],
  };
}

// ---------------------------------------------------------------------------
// 管理后台用户管理 API（仅 admin —— 非 admin 调用预期会收到 403，
// 由页面层以"权限不足"形式呈现给用户）。
// ---------------------------------------------------------------------------

async function apiJson<T>(
  input: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(input, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = (await readJson(res)) as { detail?: string } | null;
    const message =
      body?.detail || `${init.method ?? "GET"} ${input} failed (HTTP ${res.status})`;
    const err = new Error(message) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return ((await readJson(res)) as T) ?? ({} as T);
}

export async function listUsers(): Promise<AdminUserListResponse> {
  return apiJson<AdminUserListResponse>("/api/auth/users", { method: "GET" });
}

export async function createUser(
  payload: CreateUserPayload,
): Promise<AdminUserItem> {
  return apiJson<AdminUserItem>("/api/auth/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateUser(
  userId: number,
  payload: UpdateUserPayload,
): Promise<AdminUserItem> {
  return apiJson<AdminUserItem>(`/api/auth/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function updateUserRoles(
  userId: number,
  roles: string[],
  accessibleLines?: string[],
): Promise<AdminUserItem> {
  return apiJson<AdminUserItem>(`/api/auth/users/${userId}/roles`, {
    method: "PATCH",
    body: JSON.stringify({ roles, accessible_lines: accessibleLines }),
  });
}

export async function updateUserLines(
  userId: number,
  accessibleLines: string[],
): Promise<AdminUserItem> {
  return apiJson<AdminUserItem>(`/api/auth/users/${userId}/lines`, {
    method: "PATCH",
    body: JSON.stringify({ accessible_lines: accessibleLines }),
  });
}

export async function resetUserPassword(
  userId: number,
  payload: ResetPasswordPayload,
): Promise<ResetPasswordResponse> {
  return apiJson<ResetPasswordResponse>(
    `/api/auth/users/${userId}/reset-password`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function deactivateUser(userId: number): Promise<{ ok: boolean; message: string }> {
  return apiJson(`/api/auth/users/${userId}`, { method: "DELETE" });
}

// ---- 纯辅助函数 --------------------------------------------------------

export function isAdmin(user: CurrentUser | null): boolean {
  return !!user?.roles?.includes("admin");
}

export function isAuditor(user: CurrentUser | null): boolean {
  return !!user?.roles?.includes("auditor");
}

export function isViewer(user: CurrentUser | null): boolean {
  return !!user?.roles?.includes("viewer");
}

export function canViewLine(
  user: CurrentUser | null,
  lineId: string,
): boolean {
  if (!user) return false;
  if (isAdmin(user) || isAuditor(user) || isViewer(user)) return true;
  return (
    user.roles.includes(`bp:${lineId}`) ||
    user.accessible_lines.includes(lineId)
  );
}

export function canWriteLine(
  user: CurrentUser | null,
  lineId: string,
): boolean {
  if (!user) return false;
  if (isAdmin(user)) return true;
  return user.roles.includes(`bp:${lineId}`);
}

export function filterAccessibleLines<T extends { id: string }>(
  user: CurrentUser | null,
  all: T[],
): T[] {
  if (!user) return [];
  if (isAdmin(user) || isAuditor(user) || isViewer(user)) return all;
  const allowed = new Set<string>(user.accessible_lines);
  for (const r of user.roles) {
    if (r.startsWith("bp:")) allowed.add(r.slice(3));
  }
  return all.filter((x) => allowed.has(x.id));
}

// ---------------------------------------------------------------------------
// v2 RBAC bindings (C2, 2026-09-04)
//
// 8 roles × 2 scopes × N line_ids. The server (apps/api/app/routers/auth.py
// PATCH /api/auth/users/{id}/v2-roles) owns the cross-field validation; the
// client only displays + transports. V2_ROLES is the source of truth for
// the dropdown labels/colors in the admin UI — when the API role set grows,
// update the union in packages/types/src/index.ts first, then this list.
// ---------------------------------------------------------------------------

export interface V2RoleSpec {
  value: V2Role;
  label: string;
  color: string;
  scope: V2Scope;
}

export const V2_ROLES: V2RoleSpec[] = [
  { value: "admin",         label: "admin (集团 IT)",         color: "red",    scope: "global" },
  { value: "auditor",       label: "auditor (内审)",          color: "purple", scope: "global" },
  { value: "viewer",        label: "viewer (高管)",           color: "blue",   scope: "global" },
  { value: "fin_bp_global", label: "fin_bp_global (集团 FIN)", color: "orange", scope: "global" },
  { value: "hr_bp_global",  label: "hr_bp_global (集团 HR)",  color: "cyan",   scope: "global" },
  { value: "line_owner",    label: "line_owner (业务线总监)", color: "gold",   scope: "business_line" },
  { value: "fin_bp",        label: "fin_bp (业务线 FINBP)",   color: "lime",   scope: "business_line" },
  { value: "hr_bp",         label: "hr_bp (业务线 HRBP)",     color: "green",  scope: "business_line" },
];

export function v2RoleSpec(role: string): V2RoleSpec | undefined {
  return V2_ROLES.find((r) => r.value === role);
}

export async function getUserV2Roles(userId: number): Promise<UserV2RolesResponse> {
  return apiJson<UserV2RolesResponse>(
    `/api/auth/users/${userId}/v2-roles`,
    { method: "GET" },
  );
}

export async function updateUserV2Roles(
  userId: number,
  payload: UpdateUserV2RolesPayload,
): Promise<UserV2RolesResponse> {
  return apiJson<UserV2RolesResponse>(
    `/api/auth/users/${userId}/v2-roles`,
    { method: "PATCH", body: JSON.stringify(payload) },
  );
}
