// apps/web/app/(dashboard)/_components/Topbar.tsx
//
// Client-side top bar. Right-hand side hosts:
//   - RoleSwitcher (UI-only placeholder; no auth wiring). Roles are
//     built dynamically from the `lines` prop — see T6 review #2.
//   - User menu (UI-only placeholder; no auth wiring)

"use client";

import { Avatar, Dropdown, Space } from "antd";
import * as Icons from "@ant-design/icons";
import { RoleSwitcher } from "@fin-bp/ui";
import type { BusinessLine } from "@fin-bp/types";
import Link from "next/link";

export interface TopbarProps {
  /**
   * Business lines registered in the system. Forwarded to RoleSwitcher
   * so the role list is data-driven (Admin + one BP role per line).
   */
  lines?: BusinessLine[];
}

export function Topbar({ lines }: TopbarProps) {
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
      <RoleSwitcher lines={lines} />
      <Dropdown
        menu={{
          items: [
            { key: "profile", label: "Profile (placeholder)", disabled: true },
            { key: "settings", label: "Settings (placeholder)", disabled: true },
            { type: "divider" },
            { key: "logout", label: "Sign out (placeholder)", disabled: true },
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
          <Avatar size="small" icon={<Icons.UserOutlined />} style={{ background: "#1677ff" }} />
          <span style={{ fontSize: 13 }}>Guest</span>
        </span>
      </Dropdown>
    </Space>
  );
}
