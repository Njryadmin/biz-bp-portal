// apps/web/app/api/auth/me-tenant/route.ts
//
// BFF proxy: GET /api/auth/me-tenant → GET {API}/api/auth/me-tenant.
//
// M3 (2026-09-04) — returns the current user's tenant. Any logged-in
// user can call this (it's not admin-only). The frontend layout
// (TenantBadge + the super-admin Tenant switcher) calls this once on
// mount and renders the result.
//
// The X-Tenant-ID header is forwarded so a super admin's "preview"
// tenant shows up here, matching the rest of the API surface.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";

export async function GET(request: NextRequest) {
  const upstreamHeaders: Record<string, string> = {
    cookie: request.headers.get("cookie") ?? "",
  };
  const tenantId = request.headers.get("x-tenant-id");
  if (tenantId) upstreamHeaders["x-tenant-id"] = tenantId;
  try {
    const res = await fetch(`${BASE}/api/auth/me-tenant`, {
      method: "GET",
      headers: upstreamHeaders,
      cache: "no-store",
    });
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    return NextResponse.json(
      (data as Record<string, unknown>) ?? { detail: `upstream ${res.status}` },
      { status: res.status },
    );
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}
