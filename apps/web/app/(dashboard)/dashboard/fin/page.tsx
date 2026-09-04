// apps/web/app/(dashboard)/dashboard/fin/page.tsx
//
// E (2026-09-04): FIN 视角 dashboard. Thin wrapper over
// PerspectiveDashboard — the real UI lives in
// ./\_components/PerspectiveDashboard.tsx so the FIN / HR / Shared
// pages stay in lock-step.

"use client";

import { PerspectiveDashboard } from "../_components/PerspectiveDashboard";

export default function FinDashboardPage() {
  return (
    <PerspectiveDashboard
      view="fin"
      title="FIN 视角"
      apiPath="/api/dashboard/fin"
      description="财务域 KPI — 每个业务线一组, 数据源: business_lines/<line>/manifest.yaml 的 kpis.fin_view + kpis.shared_view。在右上角切换 FIN / HR / Shared 视角。"
      forbiddenTitle="您当前没有 FIN 视角访问权限"
      lineSubtitle="财务 KPI"
    />
  );
}
