// apps/web/app/api/auth/login/route.ts
//
// BFF proxy: POST /api/auth/login → POST {API}/api/auth/login.
// The browser hits same-origin /api/auth/login; we forward to the
// Python API and copy the httpOnly cookie onto the response so the
// browser receives it.

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function POST(request: Request) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "invalid JSON body" }, { status: 400 });
  }
  try {
    const upstream = await fetch(`${base}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      // Important: do NOT forward the browser's cookie to the
      // upstream API on /login (the user is anonymous). The
      // upstream will set a fresh Set-Cookie on the response.
    });
    const data = await upstream.json().catch(() => ({}));
    // Build the Next response; copy any Set-Cookie header from the
    // upstream response so the browser stores the httpOnly token.
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
