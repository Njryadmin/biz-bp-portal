// apps/web/app/api/alerts/profiles/route.ts
// BFF proxy: forwards GET /api/alerts/profiles (list business-line alert profiles) to the Python API.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: NextRequest) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  try {
    const res = await fetch(`${base}/api/alerts/profiles`, {
      method: "GET",
      headers: { cookie: request.headers.get("cookie") ?? "" },
      cache: "no-store",
    });
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
