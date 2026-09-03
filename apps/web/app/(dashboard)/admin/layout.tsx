// apps/web/app/(dashboard)/admin/layout.tsx
//
// 仅 admin 可见的分区。渲染一个二级头部（与主 Topbar 平级），
// 并对所有子路由执行 admin 角色守卫。非 admin 用户将被重定向
// 到 /403。
//
// 我们有意保留主仪表盘布局（Topbar + 侧边栏）以维持导航框架，
// admin 子头部只是在主框架之上增加面包屑 + "返回主页"链接。

"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { App, Button, Result, Space, Typography } from "antd";
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

  // 仍在解析当前用户时，显示中性的加载态
  // （不要直接显示"无权访问"——可能只是加载较慢）。
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

  // 完全未登录 → 跳到登录页（仪表盘布局一般会兜底处理，
  // 这里再多一道友好回退，避免深链 /admin/* 时短暂闪出错误）。
  if (user === null) {
    if (typeof window !== "undefined") {
      router.replace(`/login?from=${encodeURIComponent("/admin/users")}`);
    }
    return null;
  }

  // 已登录但不是 admin → 显示 403 页。
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
        {/* antd 的 <App> 提供 App.useApp() 所需的上下文，
            子页面（如 /admin/users、/admin/ai-models）都通过
            App.useApp() 拿到 { message, notification, modal }。
            没有这个包装时，hook 只会返回占位对象，调用
            message.error() 会直接抛错。 */}
        <App>
          {children}
        </App>
      </div>
    </div>
  );
}
