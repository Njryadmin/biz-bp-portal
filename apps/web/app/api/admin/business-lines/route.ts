// apps/web/app/api/admin/business-lines/route.ts
//
// BFF proxy: GET /api/admin/business-lines → GET {API}/api/admin/business-lines.
//
// Used by the admin UI to list every business line (with v2 metadata)
// for the business-line editor.
//
// Pattern copied from apps/web/app/api/registry/lines/route.ts.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";

export async function GET(request: NextRequest) {
  try {
    const res = await fetch(`${BASE}/api/admin/business-lines`, {
      cache: "no-store",
      headers: {
        cookie: request.headers.get("cookie") ?? "",
      },
    });
    const buf = await res.arrayBuffer();
    const respHeaders = new Headers();
    const ct = res.headers.get("content-type");
    if (ct) respHeaders.set("content-type", ct);
    return new Response(buf, {
      status: res.status,
      headers: respHeaders,
    });
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}
