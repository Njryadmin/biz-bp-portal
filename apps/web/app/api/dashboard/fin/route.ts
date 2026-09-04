// apps/web/app/api/dashboard/fin/route.ts
// BFF proxy: forwards GET /api/dashboard/fin to the Python API.
// The X-Active-View header from the browser is forwarded as-is so
// the backend's auth_v2.get_current_user_v2 records the active view
// in CurrentUserV2.active_view (used by audit / downstream consumers
// like the Copilot prompt). The URL path is still the source of
// truth for which KPI set the endpoint returns.

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
    const res = await fetch(`${base}/api/dashboard/fin`, {
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
