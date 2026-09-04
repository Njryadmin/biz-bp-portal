// apps/web/app/api/admin/tenants/route.ts
//
// BFF proxy: GET / POST /api/admin/tenants → upstream.
//
// M3 (2026-09-04) — super-admin only. Pattern copied from
// apps/web/app/api/admin/business-lines/route.ts (single-GET BFF),
// but extended to handle both GET (list) and POST (create). The
// PATCH path lives in the sibling [id]/route.ts.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";

async function readBody(request: Request): Promise<Uint8Array | null> {
  const method = request.method.toUpperCase();
  if (method === "GET" || method === "HEAD") return null;
  const buf = await request.arrayBuffer();
  return buf.byteLength > 0 ? new Uint8Array(buf) : null;
}

export async function GET(request: NextRequest) {
  try {
    const res = await fetch(`${BASE}/api/admin/tenants`, {
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

export async function POST(request: NextRequest) {
  try {
    const body = await readBody(request);
    const res = await fetch(`${BASE}/api/admin/tenants`, {
      method: "POST",
      headers: {
        cookie: request.headers.get("cookie") ?? "",
        "content-type":
          request.headers.get("content-type") ?? "application/json",
      },
      body: body ?? undefined,
      cache: "no-store",
      duplex: "half",
    } as RequestInit);
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
