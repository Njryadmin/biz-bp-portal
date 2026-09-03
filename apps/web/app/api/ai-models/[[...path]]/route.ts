// apps/web/app/api/ai-models/[[...path]]/route.ts
//
// /api/ai-models/* 的 BFF 通配代理，使管理后台在调用
// list / create / update / delete / test / set-default 时无需
// 处理 CORS 细节。
//
// 浏览器向 /api/ai-models（或 /api/ai-models/{id}、
// /api/ai-models/{id}/test）发起请求时携带 httpOnly cookie。
// BFF 转发 method + body + cookie 到 Python API，并把响应状态码
// 原样回传。

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
