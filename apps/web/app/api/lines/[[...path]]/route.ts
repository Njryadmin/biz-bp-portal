// apps/web/app/api/lines/[[...path]]/route.ts
//
// Catch-all BFF for the per-business-line endpoints. The backend mounts
// one router per line at startup (e.g. /api/lines/residential/projects,
// /api/lines/retail-leasing/market-benchmark, ...). The exact set of
// endpoints is therefore dynamic and varies by deployment; the BFF
// just forwards whatever path the browser asks for.
//
// The page code previously did:
//   fetch(`${API_BASE}${apiPrefix}/projects/${id}/dynamic-pl`)
// which on a single-host dev box works (cookie is on 127.0.0.1) but
// breaks as soon as the API and the Next app are on different hosts
// (no third-party cookies in modern browsers). Routing the page
// through this BFF keeps everything same-origin.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";

export const dynamic = "force-dynamic";
export const revalidate = 0;

async function proxy(request: NextRequest, pathSegments: string[] | undefined) {
  const path = (pathSegments ?? []).join("/");
  const search = request.nextUrl.search;
  const url = `${BASE}/api/lines/${path}${search}`;
  try {
    const init: RequestInit = {
      method: request.method,
      headers: {
        // Forward the user's auth cookie so the upstream get_current_user
        // resolves the same identity the page expects.
        cookie: request.headers.get("cookie") ?? "",
        "content-type": request.headers.get("content-type") ?? "application/json",
      },
      // Don't auto-follow redirects; the upstream can decide.
      redirect: "manual",
      // Pass through the body for non-GET methods.
      body: ["GET", "HEAD"].includes(request.method)
        ? undefined
        : await request.arrayBuffer(),
      cache: "no-store",
    };
    const res = await fetch(url, init);
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    return NextResponse.json(
      (data as Record<string, unknown>) ?? { detail: `upstream ${res.status}` },
      { status: res.status },
    );
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}

export const GET = (req: NextRequest, ctx: { params: { path?: string[] } }) =>
  proxy(req, ctx.params.path);

export const POST = (req: NextRequest, ctx: { params: { path?: string[] } }) =>
  proxy(req, ctx.params.path);

export const PUT = (req: NextRequest, ctx: { params: { path?: string[] } }) =>
  proxy(req, ctx.params.path);

export const PATCH = (req: NextRequest, ctx: { params: { path?: string[] } }) =>
  proxy(req, ctx.params.path);

export const DELETE = (req: NextRequest, ctx: { params: { path?: string[] } }) =>
  proxy(req, ctx.params.path);
