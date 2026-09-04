// apps/web/app/(dashboard)/_components/Topbar.tsx
//
// Client-side top bar. Right-hand side hosts:
//   - PerspectiveSwitcher (FIN / HR / Shared, E 2026-09-04) —
//     uses the v2 user to pick the default view; writes
//     ``biz-bp.active_view`` to localStorage on change.
//   - RoleSwitcher (now backed by /api/auth/me — read-only display of
//     the active user's roles)
//   - TenantBadge + TenantSwitcher (M3 2026-09-04) — current tenant
//     name; super admin can pick a different tenant via the modal.
//   - User menu with logout button (POST /api/auth/logout then
//     redirect to /login)

"use client";

import { Avatar, Dropdown, Space } from "antd";
import * as Icons from "@ant-design/icons";
import { RoleSwitcher } from "@biz-bp/ui";
import type { BusinessLine, V2CurrentUser } from "@biz-bp/types";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useCallback } from "react";
import type { CurrentUser } from "../../../lib/auth";
import { isAdmin, logout } from "../../../lib/auth";
import { PerspectiveSwitcher } from "./PerspectiveSwitcher";
import { TenantBadge } from "./TenantBadge";
import { TenantSwitcher } from "./TenantSwitcher";

export interface TopbarProps {
  /**
   * Business lines registered in the system. Forwarded to RoleSwitcher
   * so the role list is data-driven (Admin + one BP role per line).
   */
  lines?: BusinessLine[];
  /** Current authenticated user (or null if not loaded yet). */
  user?: CurrentUser | null;
  /**
   * v2 user with bindings + active_view. When provided, forwarded to
   * the PerspectiveSwitcher so it can pick the default view segment.
   * Optional — when absent the switcher defaults to "shared".
   */
  v2User?: V2CurrentUser | null;
}

export function Topbar({ lines, user, v2User }: TopbarProps) {
  const router = useRouter();
  const onLogout = useCallback(async () => {
    try {
      await logout();
    } catch {
      // ignore — we'll redirect regardless
    }
    router.replace("/login");
    router.refresh();
  }, [router]);

  return (
    <Space size="middle" align="center">
      {/* Cross-business-line tools */}
      <Link
        href="/sensitivity"
        style={{
          color: "rgba(255,255,255,0.85)",
          fontSize: 13,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
        title="跨业务线敏感性分析实验室"
      >
        <Icons.ExperimentOutlined />
        敏感性分析
      </Link>
      <Link
        href="/copilot"
        style={{
          color: "rgba(255,255,255,0.85)",
          fontSize: 13,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
        title="AI Copilot - 用自然语言提问,带引用追溯的答案"
      >
        <Icons.RobotOutlined />
        AI Copilot
      </Link>
      <Link
        href="/forecast"
        style={{
          color: "rgba(255,255,255,0.85)",
          fontSize: 13,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
        title="滚动预测 — 12 个月 KPI 预测(通用引擎)"
      >
        <Icons.LineChartOutlined />
        滚动预测
      </Link>
      <Link
        href="/alerts"
        style={{
          color: "rgba(255,255,255,0.85)",
          fontSize: 13,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
        title="告警中心 — 阈值/趋势/持续性告警(通用引擎)"
      >
        <Icons.AlertOutlined />
        告警中心
      </Link>
      <Link
        href="/scrapers"
        style={{
          color: "rgba(255,255,255,0.85)",
          fontSize: 13,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
        title="市场数据 — Web 抓取(国家统计局/链家/政策)配置、运行与历史"
      >
        <Icons.CloudDownloadOutlined />
        市场数据
      </Link>
      {isAdmin(user ?? null) ? (
        <>
          <Link
            href="/admin/users"
            aria-label="管理后台"
            style={{
              color: "#ffd591",
              fontSize: 13,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "2px 8px",
              border: "1px solid rgba(255,213,145,0.45)",
              borderRadius: 4,
            }}
            title="管理后台 — 用户角色与可见业务线"
          >
            <Icons.SafetyOutlined />
            管理后台
          </Link>
          <Link
            href="/admin/ai-models"
            aria-label="AI 模型"
            style={{
              color: "#ffd591",
              fontSize: 13,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "2px 8px",
              border: "1px solid rgba(255,213,145,0.45)",
              borderRadius: 4,
            }}
            title="管理后台 — 运行时切换 LLM provider"
          >
            <Icons.RobotOutlined />
            AI 模型
          </Link>
          {user?.is_super_admin ? (
            <Link
              href="/admin/tenants"
              aria-label="租户管理"
              style={{
                color: "#ffd591",
                fontSize: 13,
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "2px 8px",
                border: "1px solid rgba(255,213,145,0.45)",
                borderRadius: 4,
              }}
              title="租户管理 (super admin only)"
            >
              <Icons.ClusterOutlined />
              租户管理
            </Link>
          ) : null}
        </>
      ) : null}
      <RoleSwitcher lines={lines} activeRoles={user?.roles ?? null} />
      <PerspectiveSwitcher user={v2User ?? null} />
      {user ? <TenantBadge /> : null}
      {user?.is_super_admin ? <TenantSwitcher /> : null}
      <Dropdown
        menu={{
          items: [
            {
              key: "username",
              label: user?.display_name || user?.username || "Guest",
              disabled: true,
            },
            {
              key: "lines",
              label: `可见业务线: ${user?.accessible_lines?.length ?? 0}`,
              disabled: true,
            },
            { type: "divider" },
            {
              key: "logout",
              label: "退出登录",
              icon: <Icons.LogoutOutlined />,
              onClick: onLogout,
            },
          ],
        }}
        placement="bottomRight"
        trigger={["click"]}
      >
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            cursor: "pointer",
            color: "#fff",
          }}
        >
          <Avatar
            size="small"
            icon={<Icons.UserOutlined />}
            style={{ background: "#1677ff" }}
          />
          <span style={{ fontSize: 13 }}>
            {user?.display_name || user?.username || "Guest"}
          </span>
        </span>
      </Dropdown>
    </Space>
  );
}
