// apps/web/app/api/auth/logout/route.ts
//
// BFF proxy: POST /api/auth/logout → POST {API}/api/auth/logout.
// We forward the request AND copy the upstream Set-Cookie (which
// expires the token cookie) onto the browser response.

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function POST(request: Request) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  try {
    const upstream = await fetch(`${base}/api/auth/logout`, {
      method: "POST",
      headers: {
        // Forward the cookie so the upstream can clear the same
        // cookie it set on /login. Otherwise the upstream's
        // Set-Cookie would not match the browser's cookie name.
        cookie: request.headers.get("cookie") ?? "",
      },
    });
    const data = await upstream.json().catch(() => ({}));
    const headers = new Headers();
    headers.set("Content-Type", "application/json");
    const setCookie = upstream.headers.get("set-cookie");
    if (setCookie) {
      headers.append("set-cookie", setCookie);
    }
    return new NextResponse(JSON.stringify(data), {
      status: upstream.status,
      headers,
    });
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}
