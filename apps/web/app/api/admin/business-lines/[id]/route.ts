// apps/web/app/api/admin/business-lines/[id]/route.ts
//
// BFF proxy: GET / PATCH /api/admin/business-lines/{id} → upstream.
//
// Pattern copied from apps/web/app/api/auth/users/[id]/v2-roles/route.ts
// (handles GET + PATCH; POST / PUT / DELETE are not exposed by the
// Python admin endpoint and therefore intentionally omitted here).

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";

type Ctx = { params: { id: string } };

async function readBody(request: Request): Promise<Uint8Array | null> {
  const method = request.method.toUpperCase();
  if (method === "GET" || method === "HEAD") return null;
  const buf = await request.arrayBuffer();
  return buf.byteLength > 0 ? new Uint8Array(buf) : null;
}

async function proxy(request: Request, ctx: Ctx): Promise<Response> {
  const lineId = encodeURIComponent(ctx.params.id);
  const url = `${BASE}/api/admin/business-lines/${lineId}`;
  const method = request.method.toUpperCase();
  try {
    const headers: Record<string, string> = {
      cookie: request.headers.get("cookie") ?? "",
    };
    if (method !== "GET" && method !== "HEAD") {
      headers["content-type"] =
        request.headers.get("content-type") ?? "application/json";
    }
    const body = await readBody(request);
    const upstream = await fetch(url, {
      method,
      headers,
      body: body ?? undefined,
      cache: "no-store",
      // undici requires ``duplex: "half"`` whenever a request body is
      // forwarded. The DOM type doesn't include it yet so we cast.
      duplex: "half",
    } as RequestInit);
    const buf = await upstream.arrayBuffer();
    const respHeaders = new Headers();
    const ct = upstream.headers.get("content-type");
    if (ct) respHeaders.set("content-type", ct);
    return new Response(buf, {
      status: upstream.status,
      headers: respHeaders,
    });
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const PATCH = proxy;
