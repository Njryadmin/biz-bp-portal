// apps/web/app/api/scrapers/history/[source_id]/route.ts
// BFF proxy: GET /api/scrapers/history/{source_id}

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(
  req: Request,
  { params }: { params: { source_id: string } },
) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  const sid = encodeURIComponent(params.source_id);
  const incoming = new URL(req.url);
  const qs = incoming.searchParams.toString();
  try {
    const res = await fetch(
      `${base}/api/scrapers/history/${sid}${qs ? `?${qs}` : ""}`,
      { cache: "no-store" },
    );
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    return NextResponse.json(data ?? [], { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { detail: `upstream error: ${String(err)}` },
      { status: 502 },
    );
  }
}
