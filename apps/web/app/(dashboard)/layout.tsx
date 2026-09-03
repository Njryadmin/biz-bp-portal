// apps/web/app/(dashboard)/layout.tsx
//
// DYNAMIC, DATA-DRIVEN LAYOUT.
//
// 1. Resolves the current user from /api/auth/me (httpOnly cookie).
// 2. If unauthenticated, redirects to /login (the Next.js middleware
//    already does this, but we double-check here for safety + SSR).
// 3. Fetches the registry from /api/registry (same-origin BFF).
// 4. Filters the line list by the user's accessible_lines; the
//    SidebarMenu receives the filtered set.
// 5. The Topbar shows the real username + logout button.

'use client';

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { BusinessLine } from "@fin-bp/types";

import { SidebarMenu } from "./_components/SidebarMenu";
import { Topbar } from "./_components/Topbar";
import { getCurrentUser, type CurrentUser } from "../../lib/auth";

interface RegistryResponse {
  version?: string;
  lines: BusinessLine[];
  total_registered?: number;
}

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [lines, setLines] = useState<BusinessLine[]>([]);
  const [allLines, setAllLines] = useState<BusinessLine[]>([]);
  const [version, setVersion] = useState<string>("?");
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // 1. Resolve current user.
        const me = await getCurrentUser();
        if (cancelled) return;
        if (!me) {
          router.replace("/login?from=/dashboard");
          return;
        }
        setUser(me);
        // 2. Fetch the registry (BFF forwards to the API with the cookie).
        const res = await fetch("/api/registry", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as RegistryResponse;
        if (cancelled) return;
        const all = data.lines ?? [];
        setAllLines(all);
        setVersion(data.version ?? "?");
        // 3. Filter to the user's accessible lines so the sidebar
        // only shows what they can actually open.
        const allowed = new Set<string>(me.accessible_lines);
        // bp:<line> roles also grant access
        for (const r of me.roles) {
          if (r.startsWith("bp:")) allowed.add(r.slice(3));
        }
        // admin / viewer / auditor see all
        if (
          me.roles.includes("admin") ||
          me.roles.includes("viewer") ||
          me.roles.includes("auditor")
        ) {
          setLines(all);
        } else {
          setLines(all.filter((l) => allowed.has(l.id)));
        }
        setError(null);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 24px",
          height: 56,
          background: "#001529",
          color: "#fff",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <span style={{ fontSize: 18, fontWeight: 600 }}>Fin BP Portal</span>
          <span style={{ color: "rgba(255,255,255,0.55)", fontSize: 12 }}>
            registry v{version} · {lines.length}/{allLines.length || lines.length} business line
            {lines.length === 1 ? "" : "s"}
            {user ? ` · ${user.username}` : ""}
            {error ? ` · error: ${error}` : ""}
          </span>
        </div>
        <Topbar lines={allLines} user={user} />
      </header>
      <div style={{ display: "flex", flex: 1 }}>
        <aside
          style={{
            width: 240,
            background: "#fafafa",
            borderRight: "1px solid #f0f0f0",
            overflowY: "auto",
          }}
        >
          {lines.length === 0 ? (
            <div style={{ padding: 16, color: "#888", fontSize: 13 }}>
              {user
                ? "您当前没有可访问的业务线。请联系管理员分配权限。"
                : "Loading business lines…"}
            </div>
          ) : (
            <SidebarMenu
              lines={lines}
              accessibleLineIds={user?.accessible_lines}
            />
          )}
        </aside>
        <main style={{ flex: 1, padding: 0, background: "#f5f5f5" }}>
          {children}
        </main>
      </div>
    </div>
  );
}
