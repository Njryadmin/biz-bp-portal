// apps/web/app/(dashboard)/copilot/_components/CopilotViewSwitcher.tsx
//
// H task (2026-09-04): Copilot-local RBAC 视角切换器.
//
// Independent of the Topbar's <PerspectiveSwitcher /> — the Copilot
// may need a different lens for a single question (e.g. an HRBP
// asking "what does finance see here?") without re-routing the rest
// of the dashboard.
//
// Options are filtered by the user's v2 role bindings:
//   * "auto"       — always available; defers to Topbar's view
//   * "fin"        — visible iff user has fin_bp or fin_bp_global
//   * "hr"         — visible iff user has hr_bp  or hr_bp_global
//   * "line_owner" — visible iff user has line_owner
//   * "admin"      — visible iff user has admin
//
// Hidden for unauthenticated users (the localStorage value is
// irrelevant when no session exists — apiFetch will 401 anyway).

"use client";

import { useEffect, useMemo, useState } from "react";
import { Segmented, Tooltip } from "antd";
import type { V2CurrentUser } from "@biz-bp/types";
import { getCurrentUserV2 } from "../../../../lib/auth";

export type CopilotView = "auto" | "fin" | "hr" | "line_owner" | "admin";
export const COPILOT_VIEW_STORAGE_KEY = "biz-bp.copilot_view";

export function readCopilotView(): CopilotView {
  if (typeof window === "undefined") return "auto";
  try {
    const raw = window.localStorage.getItem(COPILOT_VIEW_STORAGE_KEY);
    if (raw === "fin" || raw === "hr" || raw === "line_owner" || raw === "admin") {
      return raw;
    }
  } catch {
    /* privacy mode / quota — fall through */
  }
  return "auto";
}

export function writeCopilotView(view: CopilotView): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(COPILOT_VIEW_STORAGE_KEY, view);
  } catch {
    /* ignore */
  }
}

export interface CopilotViewSwitcherProps {
  value: CopilotView;
  onChange: (v: CopilotView) => void;
}

export function CopilotViewSwitcher({ value, onChange }: CopilotViewSwitcherProps) {
  const [user, setUser] = useState<V2CurrentUser | null>(null);
  const [userLoaded, setUserLoaded] = useState(false);

  // One-shot fetch — Copilot does not need reactive re-derivation on
  // role changes mid-session. Re-running on each render would also
  // cause a flicker between segments.
  useEffect(() => {
    let cancelled = false;
    getCurrentUserV2()
      .then((u) => {
        if (!cancelled) {
          setUser(u);
          setUserLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setUserLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const options = useMemo<Array<{ label: string; value: CopilotView }>>(() => {
    // Auto is always available — it represents "no override" and falls
    // back to the Topbar's X-Active-View. Showing it lets the user
    // explicitly opt out of an earlier manual selection.
    const out: Array<{ label: string; value: CopilotView }> = [
      { label: "Auto", value: "auto" },
    ];
    if (user?.bindings) {
      const roles = new Set(user.bindings.map((b) => b.role));
      if (roles.has("fin_bp") || roles.has("fin_bp_global")) {
        out.push({ label: "FIN", value: "fin" });
      }
      if (roles.has("hr_bp") || roles.has("hr_bp_global")) {
        out.push({ label: "HR", value: "hr" });
      }
      if (roles.has("line_owner")) {
        out.push({ label: "业务线", value: "line_owner" });
      }
      if (roles.has("admin")) {
        out.push({ label: "Admin", value: "admin" });
      }
    }
    return out;
  }, [user]);

  // Hide the switcher when we don't yet know whether the user is
  // authenticated. This avoids a layout jump and a "select then
  // change" flicker on first paint. Once the load resolves, if the
  // user is unauthenticated we still hide — the parent page will
  // show login affordances on its own.
  if (!userLoaded) return null;
  if (!user) return null;

  // If the stored value is no longer in the option list (e.g. admin
  // was removed from the user's bindings) — silently snap back to
  // "auto" rather than rendering a broken Segmented with no match.
  const effectiveValue: CopilotView = options.some((o) => o.value === value)
    ? value
    : "auto";

  return (
    <Tooltip
      title="为 Copilot 选择 RBAC 视角(独立于 Topbar)。Auto = 跟随 Topbar"
      placement="bottom"
    >
      <Segmented<CopilotView>
        size="small"
        value={effectiveValue}
        onChange={(v) => onChange(v)}
        options={options}
      />
    </Tooltip>
  );
}
