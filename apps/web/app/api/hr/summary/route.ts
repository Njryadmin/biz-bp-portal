// apps/web/app/api/hr/summary/route.ts
//
// BFF proxy: forwards GET /api/hr/summary to the Python API.
//
// Mirrors apps/web/app/api/finance/summary/route.ts exactly — same
// query-string forwarding, same X-Active-View propagation, same
// no-store caching. The two routes are kept in separate files so each
// one documents its own upstream path and so a future HR-specific
// header (e.g. an "X-InsightBP-Schema: v2") can be added here without
// touching the finance BFF.

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
  const qs = request.nextUrl.searchParams.toString();
  const url = qs
    ? `${base}/api/hr/summary?${qs}`
    : `${base}/api/hr/summary`;
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
