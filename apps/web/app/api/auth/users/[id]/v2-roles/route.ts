// apps/web/app/api/auth/users/[id]/v2-roles/route.ts
//
// BFF proxy: forwards GET and PATCH /api/auth/users/{id}/v2-roles to the
// Python API. The browser hits same-origin /api/auth/users/{id}/v2-roles
// with its httpOnly cookie; the BFF forwards method + body + cookie to
// the Python API and returns the upstream response unchanged.
//
// Why a dedicated file (instead of relying on the existing
// /api/auth/users/[[...path]] catch-all)?
//   1. Self-documenting location — anyone looking for the v2 RBAC
//      binding endpoint can grep for "v2-roles" and find it.
//   2. Lets us add v2-specific header handling later (e.g. an
//      "X-InsightBP-Schema: v2" header) without touching the catch-all
//      that handles every other /api/auth/users/* path.
//   3. The optional catch-all route is greedy but Next.js prefers the
//      more specific path anyway, so this file wins for the exact
//      v2-roles URL.

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
  const userId = encodeURIComponent(ctx.params.id);
  const url = `${BASE}/api/auth/users/${userId}/v2-roles`;
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
    // Buffer the response (admin endpoints are tiny JSON).
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
