// apps/web/app/(dashboard)/_components/PerspectiveSwitcher.tsx
//
// E task (2026-09-04): 视角切换器 (FIN / HR / Shared). Lives in the
// topbar; selecting a segment writes `biz-bp.active_view` to
// localStorage and dispatches a `biz-bp:view-change` CustomEvent so
// the SidebarMenu can highlight the matching dashboard item without
// re-rendering the whole layout.
//
// Default segment (when localStorage is empty) is derived from the
// user's strongest role:
//   * fin_bp / fin_bp_global  → "fin"
//   * hr_bp  / hr_bp_global   → "hr"
//   * line_owner / admin / auditor / viewer → "shared"
//
// Pure UX: the server still decides data access — switching is a UI
// hint that also forwards X-Active-View to the API.

"use client";

import { Segmented, Tooltip } from "antd";
import { useEffect, useMemo, useState } from "react";
import type { V2CurrentUser, DashboardView } from "@biz-bp/types";
import { readActiveView, writeActiveView } from "../../../lib/api";

const STORAGE_KEY = "biz-bp.active_view";

/** Heuristic: pick the default view based on the user's strongest role. */
function pickDefaultView(user: V2CurrentUser | null): DashboardView {
  if (!user) return "shared";
  const roles = new Set(user.roles);
  // v1 fallback: bp:<line> → treat as "line_owner" view
  for (const r of user.roles) {
    if (r.startsWith("bp:")) {
      return "shared";
    }
  }
  if (roles.has("fin_bp") || roles.has("fin_bp_global")) return "fin";
  if (roles.has("hr_bp") || roles.has("hr_bp_global")) return "hr";
  return "shared";
}

export interface PerspectiveSwitcherProps {
  /** Optional: the v2 user. When provided, used to pick the default view. */
  user?: V2CurrentUser | null;
}

export function PerspectiveSwitcher({ user }: PerspectiveSwitcherProps) {
  // Start with the default view (derived from role). After mount, we
  // upgrade to whatever localStorage holds so returning users keep
  // their last selection.
  const initial = useMemo<DashboardView>(
    () => pickDefaultView(user ?? null),
    [user],
  );
  const [view, setView] = useState<DashboardView>(initial);

  // Hydrate from localStorage AFTER mount to avoid an SSR/CSR mismatch.
  useEffect(() => {
    const stored = readActiveView();
    if (stored && stored !== view) {
      setView(stored);
    }
    // Intentionally only on mount — re-running on user change would
    // override the user's explicit selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When user prop changes (e.g. login completed), snap to the new default
  // if the current selection is invalid for the new user.
  useEffect(() => {
    if (!user) return;
    // We don't auto-overwrite an explicit stored selection — that's a
    // user choice. But if localStorage is empty, use the new default.
    if (!readActiveView()) {
      const def = pickDefaultView(user);
      if (def !== view) setView(def);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  function onChange(next: string | number) {
    const v = String(next) as DashboardView;
    if (v !== "fin" && v !== "hr" && v !== "shared") return;
    setView(v);
    writeActiveView(v);
    if (typeof window !== "undefined") {
      window.dispatchEvent(
        new CustomEvent("biz-bp:view-change", { detail: v }),
      );
    }
  }

  return (
    <Tooltip
      title="切换 FIN / HR / 共享 视角 (X-Active-View header)"
      placement="bottom"
    >
      <Segmented
        size="small"
        value={view}
        onChange={onChange}
        options={[
          { label: "FIN", value: "fin" },
          { label: "HR", value: "hr" },
          { label: "Shared", value: "shared" },
        ]}
        style={{ background: "rgba(255,255,255,0.08)" }}
        // We don't want the segment to ever be disabled — the user
        // can always switch; the server enforces access (will 403 if
        // they don't have the right domain).
      />
    </Tooltip>
  );
}

export { STORAGE_KEY as PERSPECTIVE_STORAGE_KEY };
