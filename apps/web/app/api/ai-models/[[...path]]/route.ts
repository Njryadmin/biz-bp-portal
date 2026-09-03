// apps/web/app/api/ai-models/[[...path]]/route.ts
//
// Catch-all BFF proxy for /api/ai-models/* so the admin UI can call
// list/create/update/delete/test/set-default without CORS gymnastics.
//
// The browser POSTs /api/ai-models (or /api/ai-models/{id} or
// /api/ai-models/{id}/test) with the httpOnly cookie. The BFF
// forwards method + body + cookie to the Python API and copies the
// response status back.

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";

type Ctx = { params: { path?: string[] } };

function buildUrl(path: string[] | undefined): string {
  const tail = (path ?? []).join("/");
  return `${BASE}/api/ai-models${tail ? `/${tail}` : ""}`;
}

async function readBody(request: Request): Promise<Uint8Array | null> {
  const method = request.method.toUpperCase();
  if (method === "GET" || method === "HEAD") return null;
  const buf = await request.arrayBuffer();
  return buf.byteLength > 0 ? new Uint8Array(buf) : null;
}

async function proxy(request: Request, ctx: Ctx): Promise<Response> {
  const url = buildUrl(ctx.params.path);
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
      // Never cache — the BFF must always reflect upstream.
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
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
