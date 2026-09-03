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
