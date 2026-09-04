// apps/web/lib/tenants.ts
//
// Tenant admin / self-service API wrapper (M3, 2026-09-04).
//
// Mirrors apps/web/lib/auth.ts (same fetch helper, same error shape).
// All callers should go through these wrappers so the wire contract
// is enforced in one place.

import type {
  CreateTenantPayload,
  TenantInfo,
  TenantListResponse,
  UpdateTenantPayload,
} from "@biz-bp/types";

async function readJson(res: Response) {
  if (res.status === 204) return null;
  return res.json().catch(() => null);
}

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

export async function listTenants(): Promise<TenantListResponse> {
  return apiJson<TenantListResponse>("/api/admin/tenants", { method: "GET" });
}

export async function createTenant(
  payload: CreateTenantPayload,
): Promise<TenantInfo> {
  return apiJson<TenantInfo>("/api/admin/tenants", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateTenant(
  tenantId: string,
  payload: UpdateTenantPayload,
): Promise<TenantInfo> {
  return apiJson<TenantInfo>(`/api/admin/tenants/${tenantId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function getMyTenant(): Promise<TenantInfo | null> {
  try {
    return await apiJson<TenantInfo>("/api/auth/me-tenant", { method: "GET" });
  } catch (e) {
    // 401 → not logged in (BFF will surface), 404 → tenant row missing
    // (e.g. mid-switch). Both are "no badge" cases.
    const err = e as Error & { status?: number };
    if (err.status === 401 || err.status === 404) return null;
    throw e;
  }
}
