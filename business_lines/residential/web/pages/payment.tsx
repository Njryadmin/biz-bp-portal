// business_lines/residential/web/pages/payment.tsx
// Residential line — payment / commission / channel-fee (stacked bar).

"use client";

import { useEffect, useState } from "react";
import { Card, Col, Row, Space, Typography, Select, Spin, Statistic } from "antd";
import { UniversalChart } from "@fin-bp/ui";

const { Title, Paragraph, Text } = Typography;

interface PaymentPayload {
  project_id: string;
  project_name: string;
  months: string[];
  monthly_plan_yi: number[];
  monthly_actual_yi: number[];
  monthly_commission_yi: number[];
  monthly_channel_fee_yi: number[];
  cumulative_plan_yi: number;
  cumulative_actual_yi: number;
  payment_completion: number;
  payment_vs_plan: number;
  channel_fee_ratio: number;
}

const FALLBACK: PaymentPayload = {
  project_id: "PRJ-001",
  project_name: "上海·绿城黄浦江",
  months: ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"],
  monthly_plan_yi:        [3.2, 3.5, 3.0, 2.8, 3.1, 3.4, 3.6, 3.8, 4.0, 3.7, 3.5, 3.3],
  monthly_actual_yi:      [3.1, 3.4, 2.9, 2.7, 3.0, 3.3, 3.5, 3.7, 3.9, 3.6, 3.4, 3.2],
  monthly_commission_yi:  [0.018, 0.020, 0.018, 0.017, 0.018, 0.020, 0.021, 0.022, 0.024, 0.022, 0.020, 0.019],
  monthly_channel_fee_yi: [0.030, 0.033, 0.028, 0.026, 0.029, 0.032, 0.034, 0.036, 0.038, 0.035, 0.033, 0.031],
  cumulative_plan_yi: 41.9,
  cumulative_actual_yi: 39.7,
  payment_completion: 0.948,
  payment_vs_plan: 0.948,
  channel_fee_ratio: 0.037,
};

export default function PaymentPage() {
  const [data, setData] = useState<PaymentPayload>(FALLBACK);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
    (async () => {
      try {
        const r = await fetch(`${base}/api/lines/residential/projects/PRJ-001/payment`, { cache: "no-store" });
        if (r.ok) {
          const j = await r.json();
          setData({ ...FALLBACK, ...j });
        }
      } catch {
        /* keep fallback */
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const stackOption = {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { data: ["回款 (亿元)", "佣金 (千万元)", "渠道费 (千万元)"] },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: { type: "category", data: data.months },
    yAxis: { type: "value", name: "亿元 / 千万元" },
    series: [
      { name: "回款 (亿元)",      type: "bar", stack: "a", data: data.monthly_actual_yi,     itemStyle: { color: "#1677ff" } },
      { name: "佣金 (千万元)",    type: "bar", stack: "a", data: data.monthly_commission_yi, itemStyle: { color: "#faad14" } },
      { name: "渠道费 (千万元)",  type: "bar", stack: "a", data: data.monthly_channel_fee_yi, itemStyle: { color: "#f5222d" } },
    ],
  };

  const planActualOption = {
    tooltip: { trigger: "axis" },
    legend: { data: ["计划回款", "实际回款"] },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: { type: "category", data: data.months },
    yAxis: { type: "value", name: "亿元" },
    series: [
      { name: "计划回款", type: "line", data: data.monthly_plan_yi,   smooth: true, itemStyle: { color: "#8c8c8c" }, lineStyle: { type: "dashed" } },
      { name: "实际回款", type: "line", data: data.monthly_actual_yi, smooth: true, itemStyle: { color: "#1677ff" } },
    ],
  };

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>回款-佣金-渠道费联动</Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          {data.project_name} · 近 12 个月回款与销售费用联动分析
        </Paragraph>
      </Space>

      {loading && <Spin style={{ marginBottom: 12 }} />}

      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="回款完成率"
              value={data.payment_completion * 100}
              precision={2}
              suffix="%"
              valueStyle={{ color: data.payment_completion >= 0.9 ? "#3f8600" : "#cf1322" }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="月度回款/计划"
              value={data.payment_vs_plan * 100}
              precision={2}
              suffix="%"
              valueStyle={{ color: data.payment_vs_plan >= 0.9 ? "#3f8600" : "#cf1322" }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="渠道费占比"
              value={data.channel_fee_ratio * 100}
              precision={2}
              suffix="%"
              valueStyle={{ color: data.channel_fee_ratio <= 0.05 ? "#3f8600" : "#cf1322" }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="累计回款 / 累计计划"
              value={data.cumulative_actual_yi}
              precision={2}
              suffix="亿"
            />
            <Text type="secondary" style={{ fontSize: 12 }}>计划 {data.cumulative_plan_yi.toFixed(2)} 亿</Text>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="堆叠柱：回款 / 佣金 / 渠道费" size="small">
            <UniversalChart option={stackOption} style={{ height: 360 }} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="回款 vs 计划 (折线)" size="small">
            <UniversalChart option={planActualOption} style={{ height: 360 }} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
