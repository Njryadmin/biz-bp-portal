// apps/web/app/api/auth/accessible-lines/route.ts
//
// BFF proxy: GET /api/auth/accessible-lines → GET {API}/api/auth/accessible-lines.
//
// apps/web/lib/auth.ts exposes ``getAccessibleLines()`` which fetches
// this same-origin path; the BFF forwards the cookie so the API can
// decode the token and return the per-user visible business-line set.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: NextRequest) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  try {
    const upstream = await fetch(`${base}/api/auth/accessible-lines`, {
      method: "GET",
      headers: {
        cookie: request.headers.get("cookie") ?? "",
      },
      cache: "no-store",
    });
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(data, { status: upstream.status });
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}
