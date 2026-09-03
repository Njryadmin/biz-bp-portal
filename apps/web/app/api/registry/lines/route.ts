// apps/web/app/api/registry/lines/route.ts
//
// BFF proxy: GET /api/registry/lines → GET {API}/api/registry/lines.
//
// The browser-side code (e.g. apps/web/app/(dashboard)/[line]/[page]/page.tsx)
// calls /api/registry/lines directly to resolve the per-line api_prefix.
// We forward the cookie so the upstream can enforce RBAC.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: NextRequest) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  try {
    const res = await fetch(`${base}/api/registry/lines`, {
      cache: "no-store",
      headers: {
        cookie: request.headers.get("cookie") ?? "",
      },
    });
    if (!res.ok) {
      return NextResponse.json(
        { lines: [], error: `upstream status ${res.status}` },
        { status: 200 },
      );
    }
    const data = await res.json();
    return NextResponse.json(data, { status: 200 });
  } catch (err) {
    return NextResponse.json(
      { lines: [], error: String(err) },
      { status: 200 },
    );
  }
}
