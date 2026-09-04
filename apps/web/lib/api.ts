// apps/web/lib/api.ts
//
// Tiny shared fetch helper used by the dashboard pages (E, 2026-09-04).
// The BFF endpoints forward ``X-Active-View`` to the Python API so the
// backend's CurrentUserV2.active_view is recorded correctly for audit
// and downstream consumers (Copilot prompt etc.).
//
// M3 (2026-09-04) — also forwards ``X-Tenant-ID`` so super admin can
// switch tenant from the Topbar. The header is read from localStorage
// (``biz-bp.tenant_id``) by default; the explicit ``tenantId`` option
// is an override for code that already knows the value.
//
// Usage:
//   const data = await apiFetch<DashboardResponse>("/api/dashboard/fin", {
//     view: "fin",
//     tenantId: "<uuid>",   // override
//   });
//
// The helper reads the active view from localStorage
// (``biz-bp.active_view``) automatically — the explicit ``view`` option
// is an override for code that already knows the view (e.g. inside
// /dashboard/fin/page.tsx, which always passes "fin").

import type { DashboardView } from "@biz-bp/types";

const ACTIVE_VIEW_STORAGE_KEY = "biz-bp.active_view";
const TENANT_ID_STORAGE_KEY = "biz-bp.tenant_id";

/** Read the active view from localStorage. Safe on SSR. */
export function readActiveView(): DashboardView | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(ACTIVE_VIEW_STORAGE_KEY);
    if (!raw) return null;
    if (raw === "fin" || raw === "hr" || raw === "shared") return raw;
    return null;
  } catch {
    return null;
  }
}

/** Write the active view to localStorage. Safe on SSR. */
export function writeActiveView(view: DashboardView | null): void {
  if (typeof window === "undefined") return;
  try {
    if (view) window.localStorage.setItem(ACTIVE_VIEW_STORAGE_KEY, view);
    else window.localStorage.removeItem(ACTIVE_VIEW_STORAGE_KEY);
  } catch {
    // ignore quota / privacy-mode errors
  }
}

/** Read the active tenant id (super-admin override) from localStorage. Safe on SSR. */
export function readTenantId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TENANT_ID_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Write the active tenant id to localStorage. Safe on SSR. */
export function writeTenantId(tenantId: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (tenantId) window.localStorage.setItem(TENANT_ID_STORAGE_KEY, tenantId);
    else window.localStorage.removeItem(TENANT_ID_STORAGE_KEY);
  } catch {
    // ignore quota / privacy-mode errors
  }
}

export interface ApiFetchOptions extends Omit<RequestInit, "headers"> {
  /**
   * Active view to forward via X-Active-View.
   *
   * Default: localStorage (`biz-bp.active_view`). Pass `null` to explicitly
   * skip the header (e.g. the Copilot page's "auto" mode, which means
   * "follow the Topbar — do not override").
   *
   * Accepts the full set of backend `switch_view()` values, which is a
   * superset of the Topbar's `DashboardView` (fin / hr / shared).
   */
  view?: DashboardView | "line_owner" | "admin" | "auditor" | "viewer" | "none" | null;
  /**
   * Tenant id to forward via X-Tenant-ID. M3 (2026-09-04) — super admin
   * only. Default: localStorage (`biz-bp.tenant_id`). Pass `null` to
   * explicitly skip the header.
   */
  tenantId?: string | null;
  headers?: Record<string, string>;
}

/** Tiny GET wrapper that auto-forwards the X-Active-View + X-Tenant-ID headers. */
export async function apiFetch<T>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const view = options.view ?? readActiveView();
  const tenantId = options.tenantId ?? readTenantId();
  const headers: Record<string, string> = {
    credentials: "include",
    ...(options.headers ?? {}),
  };
  if (view) headers["x-active-view"] = view;
  if (tenantId) headers["x-tenant-id"] = tenantId;
  const res = await fetch(path, {
    ...options,
    credentials: "include",
    headers,
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as
      | { detail?: string }
      | null;
    const err = new Error(
      body?.detail || `${options.method ?? "GET"} ${path} failed (HTTP ${res.status})`,
    ) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return ((await res.json()) as T) ?? ({} as T);
}
