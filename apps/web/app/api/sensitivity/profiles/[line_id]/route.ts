// apps/web/app/api/sensitivity/profiles/[line_id]/route.ts
// BFF proxy: forwards GET /api/sensitivity/profiles/{line_id} to the Python API.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: NextRequest,
  { params }: { params: { line_id: string } },
) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  const lineId = encodeURIComponent(params.line_id);
  try {
    const res = await fetch(`${base}/api/sensitivity/profiles/${lineId}`, {
      headers: { cookie: request.headers.get("cookie") ?? "" },
            cache: "no-store",
    });
    if (!res.ok) {
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
    }
    const data = await res.json();
    return NextResponse.json(data, { status: 200 });
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}
