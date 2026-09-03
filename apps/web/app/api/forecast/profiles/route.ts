// apps/web/app/api/forecast/profiles/route.ts
// BFF proxy: forwards GET /api/forecast/profiles to the Python API.

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8769";
  try {
    const res = await fetch(`${base}/api/forecast/profiles`, { cache: "no-store" });
    if (!res.ok) {
      return NextResponse.json(
        { count: 0, profiles: [], error: `upstream status ${res.status}` },
        { status: 200 },
      );
    }
    const data = await res.json();
    return NextResponse.json(data, { status: 200 });
  } catch (err) {
    return NextResponse.json(
      { count: 0, profiles: [], error: String(err) },
      { status: 200 },
    );
  }
}
