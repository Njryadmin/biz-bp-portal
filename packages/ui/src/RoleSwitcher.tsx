// packages/ui/src/RoleSwitcher.tsx
//
// UI-only role switcher placeholder. The list of roles is GENERATED from
// the business-line registry passed via the `lines` prop — there is no
// hardcoded list of business lines here. The role set is:
//
//   {Admin} ∪ {BP-<line.display_name> | line ∈ lines}
//
// This keeps the UI component decoupled from any specific business line
// (e.g. "BP-Residential" / "BP-Retail"); adding a new line to the
// registry automatically grows the role list. See T6 review #2.

"use client";

import { useMemo, useState } from "react";
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

/**
 * Minimal shape of a business line that this component needs. We use
 * a local interface (instead of importing from @fin-bp/types) to keep
 * @fin-bp/ui free of cross-package type deps that could cycle.
 */
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

export function RoleSwitcher({
  lines,
  defaultRole,
  onChange,
  value,
}: RoleSwitcherProps) {
  const roles = useMemo(() => buildRoles(lines), [lines]);
  const [internal, setInternal] = useState<RoleName>(defaultRole ?? roles[0]);
  const current = value ?? internal;

  // If the available roles shrink below the currently-selected one
  // (e.g. registry reload with fewer lines), fall back to the first.
  const safeCurrent = roles.includes(current) ? current : roles[0];

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
