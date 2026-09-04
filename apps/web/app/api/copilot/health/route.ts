// apps/web/app/api/copilot/health/route.ts
// BFF proxy: forwards GET /api/copilot/health to the Python API.
// X-Active-View is forwarded (H, 2026-09-04) for consistency with the
// other copilot BFFs — health itself is view-agnostic but forwarding
// keeps the backend audit log coherent.

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
    const res = await fetch(`${base}/api/copilot/health`, {
      headers: upstreamHeaders,
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json(
        { backend: "unknown", available_lines: [], error: `upstream status ${res.status}` },
        { status: 200 },
      );
    }
    const data = await res.json();
    return NextResponse.json(data, { status: 200 });
  } catch (err) {
    return NextResponse.json(
      { backend: "unknown", available_lines: [], error: String(err) },
      { status: 200 },
    );
  }
}
