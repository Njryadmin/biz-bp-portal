// business_lines/retail/web/pages/efficiency.tsx
// 坪效仪表盘:跨物业的 NOI 规模 vs 坪效二维散点.

"use client";

import { useEffect, useMemo, useState } from "react";
import { Col, Row, Skeleton, Typography } from "antd";
import {
  EmptyState,
  UniversalChart,
  UniversalKpiCard,
} from "@fin-bp/ui";

const { Title, Paragraph } = Typography;

interface Property {
  property_id: string;
  name: string;
  city: string;
  format: string;
  gla_wan_sqm: number;
  total_brands: number;
  headline_kpis: {
    noi: number;
    efficiency: number;
    foot_traffic_efficiency: number;
    vacancy_rate: number;
    collection_rate: number;
    rent_escalation: number;
    wault: number;
  };
}

const API_BASE =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

export default function RetailEfficiencyPage() {
  const [items, setItems] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/lines/retail/properties`)
      .then((r) => r.json())
      .then((res) => {
        if (!cancelled) setItems(res.items ?? []);
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const avgEfficiency = useMemo(() => {
    if (items.length === 0) return 0;
    return (
      items.reduce((s, p) => s + p.headline_kpis.efficiency, 0) / items.length
    );
  }, [items]);

  const avgFoot = useMemo(() => {
    if (items.length === 0) return 0;
    return (
      items.reduce(
        (s, p) => s + p.headline_kpis.foot_traffic_efficiency,
        0,
      ) / items.length
    );
  }, [items]);

  const totalGla = useMemo(
    () => items.reduce((s, p) => s + p.gla_wan_sqm, 0),
    [items],
  );

  const scatterOption = useMemo(() => {
    return {
      tooltip: {
        trigger: "item",
        formatter: (p: {
          data: [string, number, number, string];
        }) =>
          `<b>${p.data[0]}</b><br/>NOI: ${p.data[1]} 万元<br/>坪效: ${p.data[2]} 元/㎡/月<br/>业态: ${p.data[3]}`,
      },
      grid: { left: 64, right: 24, top: 24, bottom: 64 },
      xAxis: { type: "value", name: "NOI (万元)" },
      yAxis: { type: "value", name: "坪效 (元/㎡/月)" },
      series: [
        {
          type: "scatter",
          symbolSize: (d: [string, number, number, string]) =>
            Math.max(14, d[2] * 1.2),
          data: items.map((p) => [
            p.name,
            p.headline_kpis.noi,
            p.headline_kpis.efficiency,
            p.format,
          ]),
          itemStyle: { color: "#1677ff", opacity: 0.8 },
          label: {
            show: true,
            position: "top",
            formatter: (p: { data: [string, number, number, string] }) =>
              p.data[0],
            fontSize: 10,
          },
        },
      ],
    };
  }, [items]);

  const footOption = useMemo(() => {
    return {
      tooltip: { trigger: "axis" },
      grid: { left: 56, right: 24, top: 24, bottom: 64 },
      xAxis: {
        type: "category",
        data: items.map((p) => p.name),
        axisLabel: { rotate: 30, fontSize: 11 },
      },
      yAxis: { type: "value", name: "客流坪效 (人/㎡/日)" },
      series: [
        {
          type: "bar",
          data: items.map((p) => p.headline_kpis.foot_traffic_efficiency),
          itemStyle: { color: "#13c2c2" },
          label: { show: true, position: "top", fontSize: 11 },
        },
      ],
    };
  }, [items]);

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <EmptyState title="加载失败" description={error} />
      </div>
    );
  }

  return (
    <div style={{ padding: 24 }}>
      <Title level={3} style={{ marginBottom: 4 }}>
        坪效仪表盘
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        单位面积盈利能力 + 客流密度。气泡大小 = 坪效水平,横轴 = NOI 规模,
        纵轴 = 坪效。
      </Paragraph>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="组合平均坪效"
            value={avgEfficiency}
            format="ratio"
            unit="元/㎡/月"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="组合平均客流坪效"
            value={avgFoot}
            format="ratio"
            unit="人/㎡/日"
            loading={loading}
            precision={4}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="组合总建面"
            value={totalGla}
            format="ratio"
            unit="万㎡"
            loading={loading}
            precision={1}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="物业数"
            value={items.length}
            format="number"
            loading={loading}
            precision={0}
          />
        </Col>
      </Row>

      <Title level={4} style={{ marginTop: 32 }}>
        NOI 规模 vs 坪效
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 400 }} />
      ) : (
        <UniversalChart option={scatterOption} style={{ height: 400 }} />
      )}

      <Title level={4} style={{ marginTop: 32 }}>
        各物业客流坪效
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 360 }} />
      ) : (
        <UniversalChart option={footOption} style={{ height: 360 }} />
      )}
    </div>
  );
}
