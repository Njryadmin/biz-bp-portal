// apps/web/app/(dashboard)/_components/SidebarMenu.tsx
//
// Client-side sidebar with collapsible business-line sections.
// - State persists in localStorage (key = "finbp.sidebar.collapsed")
// - Chevron rotates on toggle
// - Active line auto-opens (other lines user-closed stay closed)
// - Hover/active states for both the group header and child items

"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import { Tooltip } from "antd";
import * as Icons from "@ant-design/icons";
import Link from "next/link";
import type { BusinessLine } from "@fin-bp/types";

type IconName = keyof typeof Icons;

const STORAGE_KEY = "finbp.sidebar.collapsed";

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

function displayName(line: BusinessLine): string {
  return line.display_name ?? line.name ?? line.id;
}

function loadCollapsed(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw) as string[];
    return new Set(arr);
  } catch {
    return new Set();
  }
}

function saveCollapsed(set: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(set)));
  } catch {
    // ignore quota / privacy-mode errors
  }
}

export interface SidebarMenuProps {
  lines: BusinessLine[];
}

export function SidebarMenu({ lines }: SidebarMenuProps) {
  const pathname = usePathname() ?? "/dashboard";

  // -------------------------------------------------------------------------
  // Sort business lines by display name (zh-CN friendly)
  // -------------------------------------------------------------------------
  const sorted = useMemo(
    () =>
      [...lines].sort((a, b) =>
        displayName(a).localeCompare(displayName(b), "zh-Hans-CN", {
          sensitivity: "base",
        }),
      ),
    [lines],
  );

  // -------------------------------------------------------------------------
  // Determine which business line is "active" (i.e. owns the current path).
  // The active line's section is always force-opened; other sections honour
  // the user's localStorage state.
  // -------------------------------------------------------------------------
  const activeLineId = useMemo(() => {
    if (pathname === "/dashboard") return null;
    for (const line of sorted) {
      for (const nav of line.nav ?? []) {
        if (nav.path && (pathname === nav.path || pathname.startsWith(`${nav.path}/`))) {
          return line.id;
        }
      }
    }
    return null;
  }, [pathname, sorted]);

  // -------------------------------------------------------------------------
  // Collapsed set: hydrated from localStorage on mount; active line never
  // appears in this set.
  // -------------------------------------------------------------------------
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setCollapsed(loadCollapsed());
    setHydrated(true);
  }, []);

  // After hydration, persist the active line is open (not in collapsed set).
  useEffect(() => {
    if (!hydrated || !activeLineId) return;
    if (collapsed.has(activeLineId)) {
      const next = new Set(collapsed);
      next.delete(activeLineId);
      setCollapsed(next);
      saveCollapsed(next);
    }
  }, [hydrated, activeLineId, collapsed]);

  const toggleLine = (lineId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      saveCollapsed(next);
      return next;
    });
  };

  const isCollapsed = (lineId: string) => collapsed.has(lineId);
  const isActiveLine = (lineId: string) => activeLineId === lineId;

  // -------------------------------------------------------------------------
  // Inline styles (kept here to avoid a separate CSS file for one component)
  // -------------------------------------------------------------------------
  const palette = {
    bg: "#fafafa",
    border: "#f0f0f0",
    text: "#1f1f1f",
    textMuted: "#8c8c8c",
    primary: "#1677ff",
    hover: "#f0f5ff",
    activeBg: "#e6f4ff",
    activeText: "#1677ff",
  };

  return (
    <nav
      aria-label="Business line navigation"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: palette.bg,
        overflowY: "auto",
        overflowX: "hidden",
        padding: "8px 0",
      }}
    >
      {/* Top-level "Overview" item */}
      <Link
        href="/dashboard"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 16px",
          color: pathname === "/dashboard" ? palette.activeText : palette.text,
          background: pathname === "/dashboard" ? palette.activeBg : "transparent",
          fontWeight: pathname === "/dashboard" ? 600 : 500,
          fontSize: 14,
          textDecoration: "none",
          borderLeft: pathname === "/dashboard" ? `3px solid ${palette.primary}` : "3px solid transparent",
        }}
      >
        <Icons.DashboardOutlined style={{ fontSize: 16 }} />
        <span>Overview</span>
      </Link>

      {/* One collapsible section per business line */}
      {sorted.map((line) => {
        const closed = isCollapsed(line.id);
        const active = isActiveLine(line.id);
        const firstPath = line.nav?.[0]?.path ?? "/dashboard";
        const navItems = line.nav ?? [];

        return (
          <section
            key={line.id}
            aria-label={displayName(line)}
            style={{ marginTop: 4 }}
          >
            {/* Section header — clickable toggle */}
            <div
              role="button"
              tabIndex={0}
              aria-expanded={!closed}
              aria-controls={`section-${line.id}`}
              onClick={() => toggleLine(line.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggleLine(line.id);
                }
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 16px",
                cursor: "pointer",
                userSelect: "none",
                color: active ? palette.activeText : palette.text,
                background: active ? palette.hover : "transparent",
                fontWeight: active ? 600 : 500,
                fontSize: 13,
                borderLeft: active ? `3px solid ${palette.primary}` : "3px solid transparent",
                transition: "background 0.12s",
              }}
              onMouseEnter={(e) => {
                if (!active) e.currentTarget.style.background = palette.hover;
              }}
              onMouseLeave={(e) => {
                if (!active) e.currentTarget.style.background = "transparent";
              }}
            >
              {/* Chevron — rotates on collapse */}
              <Icons.CaretDownOutlined
                style={{
                  fontSize: 11,
                  color: palette.textMuted,
                  transform: closed ? "rotate(-90deg)" : "rotate(0deg)",
                  transition: "transform 0.18s",
                  flexShrink: 0,
                }}
              />
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {resolveIcon(line.icon)}
                <Tooltip title={firstPath} placement="right">
                  <Link
                    href={firstPath}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      color: "inherit",
                      textDecoration: "none",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {displayName(line)}
                  </Link>
                </Tooltip>
              </span>
              {active ? (
                <span
                  title="Current line"
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: 3,
                    background: palette.primary,
                    flexShrink: 0,
                  }}
                />
              ) : null}
            </div>

            {/* Section body — collapsible */}
            <div
              id={`section-${line.id}`}
              role="region"
              aria-hidden={closed}
              style={{
                overflow: "hidden",
                maxHeight: closed ? 0 : `${navItems.length * 36 + 8}px`,
                transition: "max-height 0.2s ease",
              }}
            >
              <ul
                style={{
                  listStyle: "none",
                  margin: 0,
                  padding: "4px 0 4px 32px",
                  borderLeft: `1px solid ${palette.border}`,
                  marginLeft: 24,
                }}
              >
                {navItems.map((nav) => {
                  const navPath = nav.path ?? "/dashboard";
                  const isCurrent = pathname === navPath || pathname.startsWith(`${navPath}/`);
                  return (
                    <li key={navPath}>
                      <Link
                        href={navPath}
                        title={nav.title ?? navPath}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "6px 12px",
                          margin: "1px 0",
                          borderRadius: 4,
                          color: isCurrent ? palette.activeText : palette.text,
                          background: isCurrent ? palette.activeBg : "transparent",
                          fontSize: 13,
                          fontWeight: isCurrent ? 600 : 400,
                          textDecoration: "none",
                          transition: "background 0.12s",
                        }}
                        onMouseEnter={(e) => {
                          if (!isCurrent)
                            e.currentTarget.style.background = palette.hover;
                        }}
                        onMouseLeave={(e) => {
                          if (!isCurrent)
                            e.currentTarget.style.background = "transparent";
                        }}
                      >
                        <Icons.LinkOutlined
                          style={{
                            fontSize: 10,
                            color: isCurrent ? palette.activeText : palette.textMuted,
                          }}
                        />
                        <span
                          style={{
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {nav.title ?? navPath}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          </section>
        );
      })}
    </nav>
  );
}
