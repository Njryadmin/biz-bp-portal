// apps/web/app/(dashboard)/layout.tsx
//
// 动态、数据驱动的布局。
//
// 1. 从 /api/auth/me 解析当前用户（httpOnly cookie）。
// 2. 若未登录则跳转 /login（Next.js middleware 已做一次，
//    这里再双检一次，覆盖 SSR 场景）。
// 3. 从 /api/registry 拉取注册表（同源 BFF）。
// 4. 按当前用户的 accessible_lines 过滤业务线列表；
//    SidebarMenu 只接收过滤后的子集。
// 5. Topbar 显示真实用户名 + 登出按钮。
// 6. E (2026-09-04): 同时拉 /api/auth/me-v2 拿 bindings + active_view
//    推给 Topbar (PerspectiveSwitcher 用来挑默认 view).

'use client';

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { BusinessLine, V2CurrentUser } from "@biz-bp/types";

import { SidebarMenu } from "./_components/SidebarMenu";
import { Topbar } from "./_components/Topbar";
import { getCurrentUser, getCurrentUserV2, type CurrentUser } from "../../lib/auth";

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
  const [v2User, setV2User] = useState<V2CurrentUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // 1. 解析当前用户。
        const me = await getCurrentUser();
        if (cancelled) return;
        if (!me) {
          router.replace("/login?from=/dashboard");
          return;
        }
        setUser(me);
        // 1b. v2 user — fetched in parallel; failure here is non-fatal
        // (the v1 user alone is enough to render the layout).
        getCurrentUserV2()
          .then((u) => {
            if (!cancelled) setV2User(u);
          })
          .catch(() => {
            if (!cancelled) setV2User(null);
          });
        // 2. 拉取注册表（BFF 携带 cookie 转发到 API）。
        const res = await fetch("/api/registry", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as RegistryResponse;
        if (cancelled) return;
        const all = data.lines ?? [];
        setAllLines(all);
        setVersion(data.version ?? "?");
        // 3. 按用户可访问的业务线过滤，使侧边栏只显示能打开的项。
        const allowed = new Set<string>(me.accessible_lines);
        // bp:<line> 角色也授予访问权限
        for (const r of me.roles) {
          if (r.startsWith("bp:")) allowed.add(r.slice(3));
        }
        // admin / viewer / auditor 可见全部
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
          <span style={{ fontSize: 18, fontWeight: 600 }}>Biz-BP Portal</span>
          <span style={{ color: "rgba(255,255,255,0.55)", fontSize: 12 }}>
            registry v{version} · {lines.length}/{allLines.length || lines.length} business line
            {lines.length === 1 ? "" : "s"}
            {user ? ` · ${user.username}` : ""}
            {error ? ` · error: ${error}` : ""}
          </span>
        </div>
        <Topbar lines={allLines} user={user} v2User={v2User} />
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
