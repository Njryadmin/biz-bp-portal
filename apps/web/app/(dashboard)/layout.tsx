// apps/web/app/(dashboard)/layout.tsx
//
// DYNAMIC, DATA-DRIVEN LAYOUT.
//
// Rules:
// 1. NEVER `import` any `business_lines/*` file. All line metadata is fetched
//    from the BFF proxy at /api/registry.
// 2. The left nav is rendered entirely from the registry response.
// 3. This layout is a CLIENT component because @ant-design/icons uses
//    React.createContext internally. Data is fetched client-side.
//
// Note: The original implementation used antd's Layout component, but
// combining server components with antd's barrel-optimized imports in
// Next.js 14 produces a "Could not find the module in the React Client
// Manifest" error. Plain HTML + flexbox is simpler and SSR-safe.

'use client';

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import type { BusinessLine } from "@fin-bp/types";

import { SidebarMenu } from "./_components/SidebarMenu";
import { Topbar } from "./_components/Topbar";

interface RegistryResponse {
  version?: string;
  lines: BusinessLine[];
}

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const [lines, setLines] = useState<BusinessLine[]>([]);
  const [version, setVersion] = useState<string>("?");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Use BFF proxy (same-origin, no CORS).
        const res = await fetch("/api/registry", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as RegistryResponse;
        if (!cancelled) {
          setLines(data.lines ?? []);
          setVersion(data.version ?? "?");
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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
            registry v{version} · {lines.length} business line
            {lines.length === 1 ? "" : "s"}
            {error ? ` · error: ${error}` : ""}
          </span>
        </div>
        <Topbar lines={lines} />
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
              No business lines registered. Edit
              <br />
              <code>business_lines/registry.yaml</code>
              <br />
              and restart the API.
            </div>
          ) : (
            <SidebarMenu lines={lines} />
          )}
        </aside>
        <main style={{ flex: 1, padding: 0, background: "#f5f5f5" }}>
          {children}
        </main>
      </div>
    </div>
  );
}
