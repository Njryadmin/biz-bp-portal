// apps/web/app/(dashboard)/_components/SidebarMenu.tsx
//
// Client-side sidebar. Determines the active nav key by inspecting the current
// URL with `usePathname`, so server-side rendering remains a pure data fetch.
//
// The list is sorted by `display_name` (falling back to `name` or `id`) and
// grouped per business line. The current line is visually emphasised in its
// group header.

"use client";

import { useMemo } from "react";
import { usePathname } from "next/navigation";
import { Menu } from "antd";
import * as Icons from "@ant-design/icons";
import Link from "next/link";
import type { BusinessLine } from "@fin-bp/types";

type IconName = keyof typeof Icons;

function resolveIcon(name: string | undefined): React.ReactNode {
  if (!name) return <Icons.AppstoreOutlined />;
  const iconMap = Icons as unknown as Record<string, React.ComponentType>;
  const candidates = [name, `${name}Outlined`];
  for (const key of candidates) {
    const Cmp = iconMap[key] as React.ComponentType | undefined;
    if (Cmp) return <Cmp />;
  }
  return <Icons.AppstoreOutlined />;
}

function pickIconName(line: BusinessLine): IconName | undefined {
  return line.icon as IconName | undefined;
}

function displayName(line: BusinessLine): string {
  return line.display_name ?? line.name ?? line.id;
}

function navKey(path: string): string {
  // Stable key for the Menu — strip leading slash and replace separators.
  return path.replace(/^\/+/, "").replace(/\//g, "_") || "root";
}

export interface SidebarMenuProps {
  lines: BusinessLine[];
}

export function SidebarMenu({ lines }: SidebarMenuProps) {
  const pathname = usePathname() ?? "/dashboard";

  // Sort the lines by display_name (case-insensitive, zh-CN friendly).
  const sorted = useMemo(
    () =>
      [...lines].sort((a, b) =>
        displayName(a).localeCompare(displayName(b), "zh-Hans-CN", {
          sensitivity: "base",
        }),
      ),
    [lines],
  );

  // Compute the currently-active key. Overview wins on exact /dashboard.
  let activeKey = "/dashboard";
  if (pathname !== "/dashboard") {
    outer: for (const line of sorted) {
      for (const nav of line.nav ?? []) {
        if (nav.path && (pathname === nav.path || pathname.startsWith(`${nav.path}/`))) {
          activeKey = navKey(nav.path);
          break outer;
        }
      }
    }
  } else {
    activeKey = "overview";
  }

  // Build menu items: Overview + one item group per line.
  const items = [
    {
      key: "overview",
      icon: <Icons.DashboardOutlined />,
      label: <Link href="/dashboard">Overview</Link>,
    },
    ...sorted.map((line) => {
      const isCurrentLine = (line.nav ?? []).some(
        (nav) => activeKey === navKey(nav.path),
      );
      return {
        type: "group" as const,
        key: `group-${line.id}`,
        label: (
          <Link
            href={(line.nav?.[0]?.path ?? `/dashboard`)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              color: isCurrentLine ? "#1677ff" : undefined,
              fontWeight: isCurrentLine ? 600 : 500,
            }}
            title={`Current: ${displayName(line)}`}
          >
            {resolveIcon(pickIconName(line))}
            <span>{displayName(line)}</span>
            {isCurrentLine ? (
              <Icons.CaretRightOutlined style={{ fontSize: 10, marginLeft: 2 }} />
            ) : null}
          </Link>
        ),
        children: (line.nav ?? []).map((nav) => ({
          key: navKey(nav.path),
          icon: <Icons.LinkOutlined />,
          label: <Link href={nav.path}>{nav.title}</Link>,
        })),
      };
    }),
  ];

  return (
    <Menu
      mode="inline"
      selectedKeys={[activeKey]}
      defaultOpenKeys={sorted.map((l) => `group-${l.id}`)}
      items={items}
      style={{ borderRight: 0 }}
    />
  );
}
