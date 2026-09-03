// apps/web/app/api/scrapers/detail/[source_id]/route.ts
// BFF proxy: GET /api/scrapers/{source_id} (scraper detail with history).

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: NextRequest,
  { params }: { params: { source_id: string } },
) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  const sid = encodeURIComponent(params.source_id);
  try {
    const res = await fetch(`${base}/api/scrapers/${sid}`, {
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
    return NextResponse.json(data ?? {}, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}
