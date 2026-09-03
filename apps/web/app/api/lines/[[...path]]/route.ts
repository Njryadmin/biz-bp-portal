// apps/web/app/api/lines/[[...path]]/route.ts
//
// 业务线相关端点的 BFF 通配路由。后端在启动时会为每条业务线挂载
// 各自对应的 router（例如 /api/lines/residential/projects、
// /api/lines/retail-leasing/market-benchmark 等）。端点集合
// 是动态的、随部署而异；BFF 仅负责原样转发浏览器请求的路径。
//
// 此前的页面代码直接：
//   fetch(`${API_BASE}${apiPrefix}/projects/${id}/dynamic-pl`)
// 在单主机开发环境能工作（cookie 落在 127.0.0.1 下），但只要
// API 与 Next 应用分别位于不同主机就会失败（现代浏览器不再
// 允许第三方 cookie）。把页面统一走这个 BFF，可以保持完全
// 同源。

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
        // 转发用户的认证 cookie，让上游 get_current_user
        // 解析出与页面预期一致的身份。
        cookie: request.headers.get("cookie") ?? "",
        "content-type": request.headers.get("content-type") ?? "application/json",
      },
      // 不自动跟随重定向；由上游自行决定。
      redirect: "manual",
      // 非 GET 方法透传请求体。
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
