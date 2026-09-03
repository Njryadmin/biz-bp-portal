// apps/web/app/(dashboard)/admin/layout.tsx
//
// Admin-only section. Renders a secondary header (sibling of the
// dashboard's main Topbar) and enforces the admin role guard for
// every child route. Non-admin users are redirected to /403.
//
// We deliberately keep the main dashboard layout (Topbar + sidebar)
// active so the admin user keeps their navigation chrome — the admin
// sub-header just adds breadcrumb + a "back to dashboard" link.

"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Button, Result, Space, Typography } from "antd";
import { ArrowLeftOutlined, SafetyOutlined } from "@ant-design/icons";

import { getCurrentUser, isAdmin, type CurrentUser } from "../../../lib/auth";

const { Title, Text } = Typography;

export default function AdminLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const me = await getCurrentUser();
      if (cancelled) return;
      setUser(me);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // While we're still resolving the current user, show a neutral
  // loading state (NOT a "no access" page — the user might just be
  // slow to load).
  if (user === undefined) {
    return (
      <div
        style={{
          padding: 48,
          textAlign: "center",
          color: "#888",
          fontSize: 13,
        }}
        role="status"
        aria-label="正在校验管理员权限"
      >
        正在校验管理员权限…
      </div>
    );
  }

  // Not authenticated at all → bounce to login (the dashboard
  // layout normally handles this, but we render a friendly fallback
  // so deep-links to /admin/* don't briefly flash an error).
  if (user === null) {
    if (typeof window !== "undefined") {
      router.replace(`/login?from=${encodeURIComponent("/admin/users")}`);
    }
    return null;
  }

  // Authenticated but not admin → 403 page.
  if (!isAdmin(user)) {
    return (
      <div style={{ padding: 24 }}>
        <Result
          status="403"
          title="403"
          subTitle="抱歉,该页面仅对管理员开放。"
          extra={
            <Link href="/dashboard">
              <Button type="primary">返回主页</Button>
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "#fff",
      }}
    >
      <header
        aria-label="Admin section header"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 24px",
          background: "linear-gradient(90deg, #1d3557 0%, #274c77 100%)",
          color: "#fff",
          borderBottom: "1px solid #1d3557",
        }}
      >
        <Space size="middle" align="center">
          <SafetyOutlined style={{ fontSize: 20 }} aria-hidden />
          <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.3 }}>
            <Title level={5} style={{ color: "#fff", margin: 0 }}>
              管理后台
            </Title>
            <Text style={{ color: "rgba(255,255,255,0.75)", fontSize: 12 }}>
              仅 admin 可见 · 操作用户角色与业务线访问权限
            </Text>
          </div>
        </Space>
        <Space size="small">
          <Link href="/admin/users">
            <Button
              type="default"
              size="small"
              aria-label="用户管理"
              style={{ background: "rgba(255,255,255,0.1)", color: "#fff", border: 0 }}
            >
              用户管理
            </Button>
          </Link>
          <Link href="/admin/ai-models">
            <Button
              type="default"
              size="small"
              aria-label="AI 模型"
              style={{ background: "rgba(255,255,255,0.1)", color: "#fff", border: 0 }}
            >
              AI 模型
            </Button>
          </Link>
          <Link href="/dashboard">
            <Button
              type="default"
              size="small"
              icon={<ArrowLeftOutlined />}
              aria-label="返回主页"
              style={{ background: "rgba(255,255,255,0.1)", color: "#fff", border: 0 }}
            >
              返回主页
            </Button>
          </Link>
        </Space>
      </header>
      <div style={{ flex: 1, padding: 24, background: "#f5f5f5", overflow: "auto" }}>
        {children}
      </div>
    </div>
  );
}
