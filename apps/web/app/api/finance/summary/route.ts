// apps/web/app/api/finance/summary/route.ts
//
// BFF proxy: forwards GET /api/finance/summary to the Python API.
//
// Query string handling
// ---------------------
// The ``lines`` query param drives which business lines the response
// covers. The browser sends it as part of the URL (?lines=residential,retail),
// and we forward it to the Python API verbatim via ``request.nextUrl.searchParams``
// — this preserves any encoding (commas, etc.) the user used and avoids
// re-parsing on the BFF side.
//
// X-Active-View header is forwarded so the backend's
// ``get_current_user_v2`` records the active view in ``CurrentUserV2.active_view``
// (used by audit / Copilot). The header is a hint, not a data selector —
// the URL is the source of truth for which view (fin/hr) is returned.

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
  // Forward the inbound query string as-is. Empty searchParams still
  // produces a valid upstream URL (the Python endpoint treats no
  // ``lines`` as "all 9 lines").
  const qs = request.nextUrl.searchParams.toString();
  const url = qs
    ? `${base}/api/finance/summary?${qs}`
    : `${base}/api/finance/summary`;
  try {
    const res = await fetch(url, {
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
