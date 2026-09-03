// apps/web/app/api/registry/route.ts
// Proxy to the Python API registry. Lets the browser hit /api/registry/lines
// (same-origin) and lets us CORS-isolate the Python service.
//
// IMPORTANT: forward the browser's cookie to the upstream API so the
// auth-protected /api/registry/lines endpoint can decode the token and
// return the lines accessible to the current user.

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
