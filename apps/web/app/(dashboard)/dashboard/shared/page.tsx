// apps/web/app/(dashboard)/dashboard/shared/page.tsx
//
// E (2026-09-04): Shared 视角 dashboard. Anyone authenticated can
// see this — the backend ``/api/dashboard/shared`` endpoint has no
// domain check, so it returns 200 even for users with no accessible
// lines (empty payload in that case).

"use client";

import { PerspectiveDashboard } from "../_components/PerspectiveDashboard";

export default function SharedDashboardPage() {
  return (
    <PerspectiveDashboard
      view="shared"
      title="Shared 视角"
      apiPath="/api/dashboard/shared"
      description="共享 KPI — 跨域共有, 不区分 FIN / HR 视角。在右上角切换 FIN / HR / Shared。"
      forbiddenTitle="暂无数据"
      lineSubtitle="共享 KPI"
    />
  );
}
