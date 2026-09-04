// apps/web/app/(dashboard)/_components/TenantBadge.tsx
//
// M3 (2026-09-04) — small badge that shows the current user's tenant
// name + plan. Rendered in the dashboard layout's Topbar so every
// logged-in user can see which tenant they belong to (useful for
// QA + super-admin operators switching context).
//
// The component re-uses useSWR-style fetching via a manual useEffect
// to avoid pulling in another dependency. If `data` is null we render
// nothing (avoids a layout shift while the request is in-flight).

"use client";

import { useEffect, useState } from "react";
import { Tag, Tooltip } from "antd";
import type { TenantInfo } from "@biz-bp/types";

import { getMyTenant } from "../../../lib/tenants";

const PLAN_COLORS: Record<string, string> = {
  standard: "blue",
  enterprise: "gold",
  demo: "purple",
};

export function TenantBadge() {
  const [data, setData] = useState<TenantInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const t = await getMyTenant();
        if (!cancelled) setData(t);
      } catch {
        if (!cancelled) setData(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!data) return null;
  const color = PLAN_COLORS[data.plan] ?? "default";
  return (
    <Tooltip
      title={
        <div style={{ fontSize: 12 }}>
          <div>当前租户</div>
          <div style={{ opacity: 0.7 }}>slug: {data.slug}</div>
        </div>
      }
    >
      <Tag
        color={color}
        style={{
          margin: 0,
          fontSize: 12,
          padding: "1px 8px",
          borderRadius: 4,
        }}
        aria-label={`当前租户: ${data.name}`}
      >
        {data.name}
      </Tag>
    </Tooltip>
  );
}
