// apps/web/app/api/alerts/rules/[line_id]/route.ts
// BFF proxy: forwards GET /api/alerts/rules/{line_id} to the Python API.

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(
  _req: Request,
  { params }: { params: { line_id: string } },
) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  const lineId = encodeURIComponent(params.line_id);
  try {
    const res = await fetch(`${base}/api/alerts/rules/${lineId}`, {
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
