// business_lines/retail/web/pages/index.tsx
// 零售分析业务线首页 (概览)
// 展示核心 KPI 卡 + 各物业 NOI 规模对比柱状图.
// 数据来源: GET /api/lines/retail/properties

"use client";

import { useEffect, useState } from "react";
import { Col, Row, Skeleton, Space, Typography } from "antd";
import {
  EmptyState,
  UniversalChart,
  UniversalKpiCard,
} from "@fin-bp/ui";
import type { Indicator } from "@fin-bp/types";

const { Title, Paragraph } = Typography;

interface PropertyHeadline {
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

interface PropertiesResponse {
  line_id: string;
  count: number;
  items: PropertyHeadline[];
}

const API_BASE =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

export default function RetailIndexPage() {
  const [data, setData] = useState<PropertiesResponse | null>(null);
  const [indicators, setIndicators] = useState<Indicator[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [propsRes, indRes] = await Promise.all([
          fetch(`${API_BASE}/api/lines/retail/properties`).then((r) => r.json()),
          fetch(`${API_BASE}/api/lines/retail/indicators`).then((r) => r.json()),
        ]);
        if (cancelled) return;
        setData(propsRes);
        setIndicators(indRes.indicators ?? []);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <EmptyState title="无法加载数据" description={error} />
      </div>
    );
  }

  const items = data?.items ?? [];
  const portfolioNoi = items.reduce((s, p) => s + p.headline_kpis.noi, 0);
  const portfolioGla = items.reduce((s, p) => s + p.gla_wan_sqm, 0);
  const avgCollection =
    items.length > 0
      ? items.reduce((s, p) => s + p.headline_kpis.collection_rate, 0) /
        items.length
      : 0;
  const avgVacancy =
    items.length > 0
      ? items.reduce((s, p) => s + p.headline_kpis.vacancy_rate, 0) /
        items.length
      : 0;

  const chartOption = {
    tooltip: { trigger: "axis" },
    grid: { left: 56, right: 24, top: 24, bottom: 64 },
    xAxis: {
      type: "category",
      data: items.map((p) => p.name),
      axisLabel: { rotate: 30, fontSize: 11 },
    },
    yAxis: { type: "value", name: "NOI (万元)" },
    series: [
      {
        type: "bar",
        data: items.map((p) => p.headline_kpis.noi),
        itemStyle: { color: "#1677ff" },
        label: { show: true, position: "top", fontSize: 11 },
      },
    ],
  };

  return (
    <div style={{ padding: 24 }}>
      <Title level={3} style={{ marginBottom: 4 }}>
        零售分析 · 概览
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        购物中心与街铺组合的资产级经营指标 — 围绕 NOI、坪效、收缴率与品牌组合
        展开。共 <b>{items.length}</b> 个物业,合计建面{" "}
        <b>{portfolioGla.toFixed(1)}</b> 万㎡。
      </Paragraph>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="组合 NOI (年化)"
            value={portfolioNoi}
            format="currency"
            unit="万元"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="平均收缴率"
            value={avgCollection}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="平均空置率"
            value={avgVacancy}
            format="percent"
            loading={loading}
            precision={2}
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
        各物业 NOI 规模
      </Title>
      {loading ? (
        <Skeleton.Node
          active
          style={{ width: "100%", height: 360 }}
        />
      ) : (
        <UniversalChart
          option={chartOption}
          style={{ height: 360 }}
        />
      )}

      <Title level={4} style={{ marginTop: 32 }}>
        指标库
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        本业务线共定义 <b>{indicators.length}</b> 个 KPI,数据源{" "}
        <code>mart_retail.*</code>。
      </Paragraph>
      {indicators.length > 0 ? (
        <Row gutter={[12, 12]}>
          {indicators.map((ind) => (
            <Col key={ind.id} xs={24} sm={12} md={8} lg={6}>
              <Space direction="vertical" size={2} style={{ width: "100%" }}>
                <Typography.Text strong>{ind.title}</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {ind.format} · {ind.aggregation} · {ind.unit || "—"}
                </Typography.Text>
              </Space>
            </Col>
          ))}
        </Row>
      ) : (
        <EmptyState
          title="指标库为空"
          description="请检查 business_lines/retail/indicators.yaml"
        />
      )}
    </div>
  );
}
