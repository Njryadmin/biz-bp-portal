// apps/web/app/api/auth/me-v2/route.ts
// BFF proxy: GET /api/auth/me-v2 → GET {API}/api/auth/me-v2.
// Used by PerspectiveSwitcher to learn the v2 role bindings and
// active_view. The browser cookie + X-Active-View header are
// forwarded so the backend records the view in CurrentUserV2.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: NextRequest) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  const upstreamHeaders: Record<string, string> = {
    cookie: request.headers.get("cookie") ?? "",
  };
  const activeView = request.headers.get("x-active-view");
  if (activeView) upstreamHeaders["x-active-view"] = activeView;
  try {
    const res = await fetch(`${base}/api/auth/me-v2`, {
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
