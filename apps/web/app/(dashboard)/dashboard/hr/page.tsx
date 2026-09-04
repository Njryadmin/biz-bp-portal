// apps/web/app/(dashboard)/dashboard/hr/page.tsx
//
// E (2026-09-04): HR 视角 dashboard. Thin wrapper over
// PerspectiveDashboard — see fin/page.tsx for the same pattern.

"use client";

import { PerspectiveDashboard } from "../_components/PerspectiveDashboard";

export default function HrDashboardPage() {
  return (
    <PerspectiveDashboard
      view="hr"
      title="HR 视角"
      apiPath="/api/dashboard/hr"
      description="人力域 KPI — 每个业务线一组, 数据源: business_lines/<line>/manifest.yaml 的 kpis.hr_view + kpis.shared_view。在右上角切换 FIN / HR / Shared 视角。"
      forbiddenTitle="您当前没有 HR 视角访问权限"
      lineSubtitle="人力 KPI"
    />
  );
}
