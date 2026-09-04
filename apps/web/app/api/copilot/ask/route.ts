// apps/web/app/api/copilot/ask/route.ts
// BFF proxy: forwards POST /api/copilot/ask to the Python API.
// X-Active-View is forwarded so the backend's copilot_view_prompt_suffix
// (H, 2026-09-04) can adjust the LLM system prompt to the user's view
// (FIN / HR / line_owner / admin). See apps/api/app/core/auth_v2.py.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function POST(request: NextRequest) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  let body: unknown = null;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "invalid JSON body" }, { status: 400 });
  }
  const upstreamHeaders: Record<string, string> = {
    cookie: request.headers.get("cookie") ?? "",
    "content-type": "application/json",
  };
  const activeView = request.headers.get("x-active-view");
  if (activeView) upstreamHeaders["x-active-view"] = activeView;
  try {
    const res = await fetch(`${base}/api/copilot/ask`, {
      method: "POST",
      headers: upstreamHeaders,
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    return NextResponse.json(data ?? {}, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}
