// business_lines/residential/web/pages/dynamic-pl.tsx
// Residential line — dynamic P&L calculator with parameter sliders.

"use client";

import { useMemo, useState } from "react";
import { Card, Col, Row, Slider, Space, Typography, Select, Divider } from "antd";
import { UniversalKpiCard } from "@fin-bp/ui";
import { UniversalChart } from "@fin-bp/ui";

const { Title, Paragraph, Text } = Typography;

interface PlPayload {
  project_id: string;
  project_name: string;
  gross_sales_yi: number;
  dynamic_cost_yi: number;
  land_cost_yi: number;
  channel_fee_yi: number;
  commission_yi: number;
  tax_yi: number;
  net_profit_yi: number;
  irr: number;
  net_margin: number;
  project_roi: number;
  monthly_dedup_rate: number;
}

// Mock data — replaced by API call in production.
const FALLBACK_PROJECTS: PlPayload[] = [
  { project_id: "PRJ-001", project_name: "上海·绿城黄浦江", gross_sales_yi: 98.13, dynamic_cost_yi: 78.6, land_cost_yi: 42.0, channel_fee_yi: 0.365, commission_yi: 0.248, tax_yi: 4.91, net_profit_yi: -27.99, irr: -0.080, net_margin: -0.285, project_roi: -0.232, monthly_dedup_rate: 0.053 },
  { project_id: "PRJ-003", project_name: "深圳·华润前海",   gross_sales_yi: 144.40, dynamic_cost_yi: 118.4, land_cost_yi: 68.0, channel_fee_yi: 0.468, commission_yi: 0.312, tax_yi: 7.22, net_profit_yi: -50.00, irr: -0.10,  net_margin: -0.346, project_roi: -0.268, monthly_dedup_rate: 0.063 },
  { project_id: "PRJ-005", project_name: "成都·保利天府",   gross_sales_yi: 47.95,  dynamic_cost_yi: 38.6, land_cost_yi: 12.5, channel_fee_yi: 0.132, commission_yi: 0.098, tax_yi: 2.40, net_profit_yi: -5.78,  irr: -0.07,  net_margin: -0.121, project_roi: -0.113, monthly_dedup_rate: 0.067 },
];

export default function DynamicPlPage() {
  const [projectId, setProjectId] = useState<string>(FALLBACK_PROJECTS[0].project_id);
  const base = FALLBACK_PROJECTS.find((p) => p.project_id === projectId) ?? FALLBACK_PROJECTS[0];

  // 三个滑块参数：售价扰动 / 成本扰动 / 去化加速
  const [priceShock, setPriceShock] = useState(0);    // [-20, +20] %
  const [costShock,  setCostShock]  = useState(0);    // [-15, +15] %
  const [dedupAccel, setDedupAccel] = useState(0);    // [-50, +50] %

  const result = useMemo(() => {
    const gross = base.gross_sales_yi * (1 + priceShock / 100);
    const cost  = (base.dynamic_cost_yi + base.land_cost_yi) * (1 + costShock / 100);
    const ch    = base.channel_fee_yi * (1 + priceShock / 100); // 售价与渠道费同向
    const comm  = base.commission_yi * (1 + priceShock / 100);
    const tax   = gross * 0.05;
    const net   = gross - cost - ch - comm - tax;
    const netMargin = gross > 0 ? net / gross : 0;
    const irr = (Math.pow(Math.max(gross / Math.max(cost, 0.01), 0.01), 1 / 3) - 1) * Math.sign(netMargin);
    const roi = net / Math.max(cost, 0.01);
    const dedup = base.monthly_dedup_rate * (1 + dedupAccel / 100);
    return { gross, cost, ch, comm, tax, net, netMargin, irr, roi, dedup };
  }, [base, priceShock, costShock, dedupAccel]);

  // 敏感性曲线：售价扰动 [-20, +20] 步长 5
  const sensitivity = useMemo(() => {
    const xs: number[] = [];
    const irrSeries: number[] = [];
    const marginSeries: number[] = [];
    for (let s = -20; s <= 20; s += 5) {
      xs.push(s);
      const g = base.gross_sales_yi * (1 + s / 100);
      const c = (base.dynamic_cost_yi + base.land_cost_yi) * (1 + costShock / 100);
      const ch = base.channel_fee_yi * (1 + s / 100);
      const cm = base.commission_yi * (1 + s / 100);
      const tax = g * 0.05;
      const net = g - c - ch - cm - tax;
      const nm = g > 0 ? net / g : 0;
      const ir = (Math.pow(Math.max(g / Math.max(c, 0.01), 0.01), 1 / 3) - 1) * Math.sign(nm);
      irrSeries.push(Number((ir * 100).toFixed(2)));
      marginSeries.push(Number((nm * 100).toFixed(2)));
    }
    return { xs, irrSeries, marginSeries };
  }, [base, costShock]);

  const chartOption = {
    tooltip: { trigger: "axis" },
    legend: { data: ["IRR (%)", "净利率 (%)"] },
    xAxis: { type: "category", data: sensitivity.xs.map((x) => `${x > 0 ? "+" : ""}${x}%`) },
    yAxis: { type: "value" },
    series: [
      { name: "IRR (%)",     type: "line", data: sensitivity.irrSeries,   smooth: true, itemStyle: { color: "#1677ff" } },
      { name: "净利率 (%)", type: "line", data: sensitivity.marginSeries, smooth: true, itemStyle: { color: "#52c41a" } },
    ],
  };

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>动态利润测算</Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          售价、成本、去化三参数扰动下的 IRR / 净利率 / ROI 即时测算。
        </Paragraph>
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col span={6}>
            <Text strong>项目</Text>
            <Select
              value={projectId}
              onChange={setProjectId}
              style={{ width: "100%", marginTop: 4 }}
              options={FALLBACK_PROJECTS.map((p) => ({ value: p.project_id, label: p.project_name }))}
            />
          </Col>
          <Col span={6}>
            <Text strong>售价扰动</Text>
            <Slider min={-20} max={20} value={priceShock} onChange={setPriceShock} marks={{ "-20%": -20, "0": 0, "+20%": 20 }} />
          </Col>
          <Col span={6}>
            <Text strong>成本扰动</Text>
            <Slider min={-15} max={15} value={costShock} onChange={setCostShock} marks={{ "-15%": -15, "0": 0, "+15%": 15 }} />
          </Col>
          <Col span={6}>
            <Text strong>去化加速</Text>
            <Slider min={-50} max={50} value={dedupAccel} onChange={setDedupAccel} marks={{ "-50%": -50, "0": 0, "+50%": 50 }} />
          </Col>
        </Row>
      </Card>

      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}><UniversalKpiCard title="动态 IRR"   value={result.irr}         format="percent" precision={2} /></Col>
        <Col xs={12} md={6}><UniversalKpiCard title="动态净利率" value={result.netMargin}   format="percent" precision={2} /></Col>
        <Col xs={12} md={6}><UniversalKpiCard title="项目级 ROI" value={result.roi}         format="percent" precision={2} /></Col>
        <Col xs={12} md={6}><UniversalKpiCard title="月度去化率" value={result.dedup}       format="percent" precision={2} /></Col>
      </Row>

      <Divider style={{ margin: "12px 0" }} />

      <Card title="售价扰动敏感性" size="small">
        <UniversalChart option={chartOption} style={{ height: 360 }} />
      </Card>
    </div>
  );
}
