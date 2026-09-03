// apps/web/middleware.ts
//
// Next.js 14 middleware: enforce authentication for all routes under
// /(dashboard)/* and any other non-public paths.
//
// What this does
// --------------
// 1. Reads the ``finbp_token`` cookie. If present, allows the request
//    through (the API will still verify the token).
// 2. If absent, redirects to ``/login?from=<original-path>``.
// 3. Public paths (login, 403, static assets, BFF auth routes) are
//    exempt so the login page itself is reachable.
//
// We deliberately do NOT verify the JWT signature here — that's the
// API's job. A signed cookie is enough to skip the redirect; an
// attacker forging a cookie will be 401'd at the API layer.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const COOKIE_NAME = process.env.FIN_BP_COOKIE_NAME || "finbp_token";

// Public paths that don't require auth. Anything else under (dashboard)
// or /api/* (except the BFF auth routes) is protected.
const PUBLIC_PREFIXES: readonly string[] = [
  "/login",
  "/403",
  "/_next",
  "/favicon",
  "/api/auth/login",
  "/api/auth/logout",
];

function isPublicPath(path: string): boolean {
  for (const p of PUBLIC_PREFIXES) {
    if (path === p || path.startsWith(`${p}/`) || path.startsWith(`${p}?`)) {
      return true;
    }
  }
  return false;
}

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  const token = request.cookies.get(COOKIE_NAME)?.value;
  if (token) {
    return NextResponse.next();
  }

  // No cookie → redirect to /login with the original path.
  const loginUrl = new URL("/login", request.url);
  // Preserve only path (not full URL with host) so the redirect never
  // bounces to an attacker-controlled host.
  loginUrl.searchParams.set("from", pathname + (search || ""));
  return NextResponse.redirect(loginUrl);
}

// Apply to everything except obvious static assets. We intentionally
// do NOT include "_next/static" or public images here so the matcher
// stays simple; the public-prefix check above handles them.
export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - api/auth/login (the BFF login endpoint itself)
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     */
    "/((?!_next/static|_next/image|favicon.ico).*)",
  ],
};
