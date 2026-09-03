// apps/web/lib/auth.ts
//
// Client-side helpers for the RBAC system. Pure functions + thin
// fetch wrappers; no React here so they can be reused in any
// component (including server components, via the ``accessToken``
// helper for SSR).

export type RoleName = string;

export interface CurrentUser {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  is_active: boolean;
  roles: RoleName[];
  accessible_lines: string[];
}

export interface AccessibleLines {
  count: number;
  lines: string[];
  all_lines: string[];
}

// ---------------------------------------------------------------------------
// Admin user-management payloads (mirror of apps/api/app/schemas/auth.py)
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
  // Set to true to explicitly clear the user's email column to NULL.
  // Pydantic EmailStr rejects empty strings so we use a separate
  // signal. The backend treats clear_email=true as "set email to
  // NULL", which wins over any value in the email field.
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
// Admin user-management API (admin only — calls are expected to 403 for
// non-admin callers; we let the page surface that as a permissions error).
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

// ---- Pure helpers --------------------------------------------------------

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
