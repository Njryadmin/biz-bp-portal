// apps/web/app/api/copilot/health/route.ts
// BFF proxy: forwards GET /api/copilot/health to the Python API.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: NextRequest) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  try {
    const res = await fetch(`${base}/api/copilot/health`, {
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json(
        { backend: "unknown", available_lines: [], error: `upstream status ${res.status}` },
        { status: 200 },
      );
    }
    const data = await res.json();
    return NextResponse.json(data, { status: 200 });
  } catch (err) {
    return NextResponse.json(
      { backend: "unknown", available_lines: [], error: String(err) },
      { status: 200 },
    );
  }
}
