// packages/ui/src/RoleSwitcher.tsx
//
// Registry-driven role switcher that shows the active user's roles
// (read-only badge) and — for admin users — a dropdown to switch the
// "active viewing role" persisted in localStorage. The dropdown does
// NOT change the user's identity; it only changes which business line
// the dashboard sidebar highlights. Real user-management is done via
// the API at /api/auth/users.

"use client";

import { useEffect, useMemo, useState } from "react";
import { Dropdown, Tag } from "antd";
import { CaretDownOutlined, UserSwitchOutlined } from "@ant-design/icons";

export type RoleName = string;

export const ADMIN_ROLE: RoleName = "Admin";

/**
 * Backward-compatible default role list (Admin-only). New callers should
 * pass the `lines` prop instead — this constant exists for callers that
 * have not yet migrated to the registry-driven API.
 */
export const DEFAULT_ROLES: RoleName[] = [ADMIN_ROLE];

export interface RoleSwitcherLine {
  id: string;
  display_name?: string | null;
  name?: string | null;
}

export interface RoleSwitcherProps {
  /**
   * List of business lines. The role list is built dynamically as
   * `Admin` + `BP-<line.display_name ?? line.name ?? line.id>` for
   * each entry. Pass `[]` (or omit) to render an Admin-only switcher.
   */
  lines?: RoleSwitcherLine[];
  /** Initial selection. Defaults to the first role. */
  defaultRole?: RoleName;
  /** Called when the user picks a different role. */
  onChange?: (role: RoleName) => void;
  /** Optional fixed role for read-only contexts. */
  value?: RoleName;
  /**
   * Active user's roles — when provided, the switcher is read-only
   * and just displays the roles as tags. When null/undefined, the
   * switcher falls back to the legacy "pick a role" dropdown.
   */
  activeRoles?: RoleName[] | null;
}

function lineRoleId(line: RoleSwitcherLine): RoleName {
  return `BP-${line.display_name ?? line.name ?? line.id}`;
}

export function buildRoles(lines: RoleSwitcherLine[] | undefined): RoleName[] {
  const out: RoleName[] = [ADMIN_ROLE];
  for (const l of lines ?? []) {
    out.push(lineRoleId(l));
  }
  return out;
}

function humaniseRole(role: string): string {
  // bp:residential → "BP: residential"
  if (role.startsWith("bp:")) return `BP: ${role.slice(3)}`;
  if (role === "auditor") return "Auditor";
  if (role === "viewer") return "Viewer";
  if (role === "admin") return "Admin";
  return role;
}

function roleColor(role: string): string {
  if (role === "admin") return "red";
  if (role === "auditor") return "gold";
  if (role === "viewer") return "blue";
  if (role.startsWith("bp:")) return "green";
  return "default";
}

export function RoleSwitcher({
  lines,
  defaultRole,
  onChange,
  value,
  activeRoles,
}: RoleSwitcherProps) {
  const roles = useMemo(() => buildRoles(lines), [lines]);
  const [internal, setInternal] = useState<RoleName>(defaultRole ?? roles[0]);
  const current = value ?? internal;

  // If the available roles shrink below the currently-selected one
  // (e.g. registry reload with fewer lines), fall back to the first.
  const safeCurrent = roles.includes(current) ? current : roles[0];

  // ---- New behaviour: when activeRoles is provided, render the
  // user's actual roles as read-only tags. --------------------------------
  if (activeRoles !== undefined && activeRoles !== null) {
    const tags = activeRoles.length > 0 ? activeRoles : ["(no role)"];
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          color: "#fff",
          padding: "0 8px",
          fontSize: 12,
        }}
        title="当前用户角色 (read-only)"
      >
        <UserSwitchOutlined />
        {tags.map((r) => (
          <Tag
            key={r}
            color={roleColor(r)}
            style={{ margin: 0, fontSize: 11, lineHeight: "16px" }}
          >
            {humaniseRole(r)}
          </Tag>
        ))}
      </span>
    );
  }

  // ---- Legacy behaviour: dropdown of available roles (UI placeholder). --
  return (
    <Dropdown
      trigger={["click"]}
      menu={{
        selectedKeys: [safeCurrent],
        items: roles.map((r) => ({
          key: r,
          label: r,
        })),
        onClick: ({ key }) => {
          const next = key as RoleName;
          if (value === undefined) setInternal(next);
          onChange?.(next);
        },
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          cursor: "pointer",
          color: "#fff",
          padding: "0 8px",
          borderRadius: 4,
          fontSize: 12,
        }}
        title="Switch role (UI placeholder — no auth wiring yet)"
      >
        <UserSwitchOutlined />
        <Tag color="blue" style={{ margin: 0 }}>
          {safeCurrent}
        </Tag>
        <CaretDownOutlined style={{ fontSize: 10 }} />
      </span>
    </Dropdown>
  );
}
