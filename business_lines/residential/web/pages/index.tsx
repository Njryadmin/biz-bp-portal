// business_lines/residential/web/pages/index.tsx
// Residential line — overview: KPI cards + project list (AG Grid).
//
// Integration note: this file is the business line's "page source". To
// expose it under the dashboard, mount it at /residential (the manifest's
// `nav` path). The cleanest path for now is a thin Next.js page in
// apps/web/app/(dashboard)/residential/page.tsx that re-exports this
// component; the constraint of this task is that the *core* dashboard
// files stay untouched.

"use client";

import { useEffect, useState } from "react";
import { Card, Col, Row, Typography, Space, Tag, Spin } from "antd";
import { UniversalKpiCard } from "@fin-bp/ui";
import { UniversalAgGrid } from "@fin-bp/ui";
import type { ColDef } from "ag-grid-community";
import type { Indicator } from "@fin-bp/types";

const { Title, Paragraph } = Typography;

interface KpiPayload {
  indicator_id: string;
  title: string;
  unit: string;
  format: "currency" | "number" | "percent" | "ratio";
  value: number;
}

interface ProjectRow {
  project_id: string;
  name: string;
  city: string;
  developer: string;
  stage: string;
  saleable_area_wan_sqm: number;
  avg_price_per_sqm: number;
  dynamic_cost_yi: number;
  cumulative_payment_yi: number;
}

const FALLBACK_KPIS: KpiPayload[] = [
  { indicator_id: "dynamic_irr",            title: "动态 IRR",     unit: "%",  format: "percent", value: 0.182 },
  { indicator_id: "dynamic_net_margin",     title: "动态净利率",   unit: "%",  format: "percent", value: 0.124 },
  { indicator_id: "payment_completion",     title: "回款完成率",   unit: "%",  format: "percent", value: 0.913 },
  { indicator_id: "channel_fee_ratio",      title: "渠道费占比",   unit: "%",  format: "percent", value: 0.038 },
  { indicator_id: "monthly_dedup_rate",     title: "月度去化率",   unit: "%",  format: "percent", value: 0.057 },
  { indicator_id: "project_roi",            title: "项目级 ROI",   unit: "%",  format: "percent", value: 0.215 },
];

const FALLBACK_PROJECTS: ProjectRow[] = [
  { project_id: "PRJ-001", name: "上海·绿城黄浦江", city: "上海", developer: "绿城中国", stage: "在建", saleable_area_wan_sqm: 12.5, avg_price_per_sqm: 78500, dynamic_cost_yi: 78.6, cumulative_payment_yi: 51.3 },
  { project_id: "PRJ-002", name: "北京·万科海淀",   city: "北京", developer: "万科地产", stage: "在建", saleable_area_wan_sqm:  9.8, avg_price_per_sqm: 82000, dynamic_cost_yi: 64.2, cumulative_payment_yi: 38.7 },
  { project_id: "PRJ-003", name: "深圳·华润前海",   city: "深圳", developer: "华润置地", stage: "在建", saleable_area_wan_sqm: 15.2, avg_price_per_sqm: 95000, dynamic_cost_yi: 118.4, cumulative_payment_yi: 72.6 },
  { project_id: "PRJ-004", name: "杭州·龙湖滨江",   city: "杭州", developer: "龙湖集团", stage: "在建", saleable_area_wan_sqm: 18.6, avg_price_per_sqm: 52000, dynamic_cost_yi: 72.5, cumulative_payment_yi: 48.9 },
];

const COLUMNS: ColDef<ProjectRow>[] = [
  { field: "project_id", headerName: "项目编号", width: 110, pinned: "left" },
  { field: "name",        headerName: "项目名称", width: 200 },
  { field: "city",        headerName: "城市",     width: 90 },
  { field: "developer",   headerName: "开发商",   width: 130 },
  { field: "stage",       headerName: "阶段",     width: 90, cellRenderer: (p: { value: string }) => <Tag color="blue">{p.value}</Tag> },
  { field: "saleable_area_wan_sqm", headerName: "可售面积(万㎡)", width: 130, type: "numericColumn" },
  { field: "avg_price_per_sqm",     headerName: "均价(元/㎡)",   width: 130, type: "numericColumn",
    valueFormatter: (p: { value: number }) => p.value?.toLocaleString("zh-CN") ?? "" },
  { field: "dynamic_cost_yi",       headerName: "动态成本(亿)",  width: 130, type: "numericColumn",
    valueFormatter: (p: { value: number }) => p.value?.toFixed(1) ?? "" },
  { field: "cumulative_payment_yi", headerName: "累计回款(亿)",  width: 130, type: "numericColumn",
    valueFormatter: (p: { value: number }) => p.value?.toFixed(1) ?? "" },
];

export default function ResidentialOverview() {
  const [kpis, setKpis] = useState<KpiPayload[]>(FALLBACK_KPIS);
  const [projects, setProjects] = useState<ProjectRow[]>(FALLBACK_PROJECTS);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
    (async () => {
      try {
        const [kRes, pRes] = await Promise.all([
          fetch(`${base}/api/lines/residential/indicators`, { cache: "no-store" }),
          fetch(`${base}/api/lines/residential/projects`,   { cache: "no-store" }),
        ]);
        if (alive && kRes.ok) {
          const k = await kRes.json();
          if (Array.isArray(k.indicators) && k.indicators.length) setKpis(k.indicators);
        }
        if (alive && pRes.ok) {
          const p = await pRes.json();
          if (Array.isArray(p.projects) && p.projects.length) setProjects(p.projects);
        }
      } catch {
        /* fall back to mock data */
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>住宅分析 · 概览</Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          业务线整体健康度：动态利润、回款、渠道费、去化、ROI 关键指标。
        </Paragraph>
      </Space>

      {loading && <Spin style={{ marginBottom: 12 }} />}

      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        {kpis.map((k) => (
          <Col key={k.indicator_id} xs={12} sm={8} md={6} lg={4}>
            <UniversalKpiCard
              title={k.title}
              value={k.value}
              format={k.format}
              unit={k.unit}
              loading={loading}
            />
          </Col>
        ))}
      </Row>

      <Card title="项目列表" size="small" bodyStyle={{ padding: 0 }}>
        <UniversalAgGrid<ProjectRow>
          rowData={projects}
          columnDefs={COLUMNS}
          style={{ height: 420 }}
          paginationPageSize={10}
        />
      </Card>
    </div>
  );
}
