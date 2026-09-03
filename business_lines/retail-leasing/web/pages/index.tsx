// business_lines/retail-leasing/web/pages/index.tsx
// 零售租赁与市场报告 · 业务线概览
// 8 个 KPI 卡(交易 + 业主 + 市场研究) + 商铺列表(UniverseKpiCard + UniversalChart).
// 数据来源: GET /api/lines/retail-leasing/properties + /indicators

"use client";

import { useEffect, useMemo, useState } from "react";
import { Col, Row, Skeleton, Space, Table, Typography } from "antd";
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
  area_district: string;
  gla_sqm: number;
  deal_rent_yuan_per_sqm_per_month: number;
  benchmark_rent_yuan_per_sqm_per_month: number;
  vacancy_rate: number;
  owner: string;
  tenant: string;
  owner_vacancy_days: number;
  quarterly_reports_published: number;
  brand_entry_rate: number;
  renewal_rate: number;
  commission_revenue_wan: number;
  headline_kpis: {
    occupancy_rate: number;
    avg_deal_rent: number;
    benchmark_gap_pct: number;
    owner_vacancy_days: number;
    quarterly_market_reports: number;
    brand_entry_rate: number;
    renewal_rate: number;
    commission_revenue: number;
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

export default function RetailLeasingIndexPage() {
  const [data, setData] = useState<PropertiesResponse | null>(null);
  const [indicators, setIndicators] = useState<Indicator[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [propsRes, indRes] = await Promise.all([
          fetch(`${API_BASE}/api/lines/retail-leasing/properties`).then((r) =>
            r.json()
          ),
          fetch(`${API_BASE}/api/lines/retail-leasing/indicators`).then((r) =>
            r.json()
          ),
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

  const portfolioStats = useMemo(() => {
    const items = data?.items ?? [];
    if (items.length === 0) {
      return {
        portfolioGla: 0,
        portfolioDealRent: 0,
        avgOccupancy: 0,
        avgBenchmarkGap: 0,
        totalCommission: 0,
      };
    }
    const totalGla = items.reduce((s, p) => s + p.gla_sqm, 0);
    const dealRentGla = items.reduce(
      (s, p) => s + p.deal_rent_yuan_per_sqm_per_month * p.gla_sqm,
      0
    );
    const portfolioDealRent =
      totalGla > 0 ? dealRentGla / totalGla : 0;
    const avgOccupancy =
      items.reduce((s, p) => s + p.headline_kpis.occupancy_rate, 0) /
      items.length;
    const avgBenchmarkGap =
      items.reduce((s, p) => s + p.headline_kpis.benchmark_gap_pct, 0) /
      items.length;
    const totalCommission = items.reduce(
      (s, p) => s + p.commission_revenue_wan,
      0
    );
    return {
      portfolioGla: totalGla,
      portfolioDealRent,
      avgOccupancy,
      avgBenchmarkGap,
      totalCommission,
    };
  }, [data]);

  const chartOption = useMemo(() => {
    const items = data?.items ?? [];
    return {
      tooltip: { trigger: "axis" },
      grid: { left: 60, right: 24, top: 24, bottom: 64 },
      xAxis: {
        type: "category",
        data: items.map((p) => p.name),
        axisLabel: { rotate: 25, fontSize: 11 },
      },
      yAxis: {
        type: "value",
        name: "成交租金 (元/㎡/月)",
      },
      series: [
        {
          type: "bar",
          data: items.map((p) => p.deal_rent_yuan_per_sqm_per_month),
          itemStyle: { color: "#13c2c2" },
          label: { show: true, position: "top", fontSize: 11 },
        },
      ],
    };
  }, [data]);

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <EmptyState title="无法加载数据" description={error} />
      </div>
    );
  }

  const items = data?.items ?? [];

  return (
    <div style={{ padding: 24 }}>
      <Title level={3} style={{ marginBottom: 4 }}>
        零售租赁与市场报告 · 概览
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        商铺出租率、成交租金、竞品对标、业主空置期与季度市场报告的组合看板。
        共 <b>{items.length}</b> 个在管商铺,合计建面{" "}
        <b>{portfolioStats.portfolioGla.toFixed(0)}</b> ㎡。
      </Paragraph>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="平均出租率"
            value={portfolioStats.avgOccupancy}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="组合成交租金"
            value={portfolioStats.portfolioDealRent}
            format="currency"
            unit="元/㎡/月"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="平均基准对标差"
            value={portfolioStats.avgBenchmarkGap}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="佣金收入合计"
            value={portfolioStats.totalCommission}
            format="currency"
            unit="万元"
            loading={loading}
            precision={1}
          />
        </Col>
      </Row>

      <Title level={4} style={{ marginTop: 32 }}>
        各商铺成交租金
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 360 }} />
      ) : (
        <UniversalChart option={chartOption} style={{ height: 360 }} />
      )}

      <Title level={4} style={{ marginTop: 32 }}>
        在管商铺列表
      </Title>
      {items.length > 0 ? (
        <Table<PropertyHeadline>
          rowKey="property_id"
          dataSource={items}
          pagination={false}
          size="small"
          columns={[
            { title: "商铺", dataIndex: "name", key: "name", width: 240 },
            {
              title: "城市/区",
              key: "city",
              width: 160,
              render: (_v, p) => `${p.city} · ${p.area_district}`,
            },
            { title: "业主", dataIndex: "owner", key: "owner", width: 160 },
            {
              title: "成交租金",
              dataIndex: "deal_rent_yuan_per_sqm_per_month",
              key: "deal_rent",
              width: 120,
              render: (v: number) => `${v} 元/㎡/月`,
            },
            {
              title: "基准对标差",
              dataIndex: ["headline_kpis", "benchmark_gap_pct"],
              key: "gap",
              width: 120,
              render: (v: number) => `${(v * 100).toFixed(2)}%`,
            },
            {
              title: "出租率",
              dataIndex: ["headline_kpis", "occupancy_rate"],
              key: "occ",
              width: 100,
              render: (v: number) => `${(v * 100).toFixed(1)}%`,
            },
            {
              title: "业主空置期",
              dataIndex: "owner_vacancy_days",
              key: "vac",
              width: 110,
              render: (v: number) => `${v} 天`,
            },
          ]}
        />
      ) : (
        <EmptyState
          title="暂无在管商铺"
          description="请检查 business_lines/retail-leasing/data/seed/properties.json"
        />
      )}

      <Title level={4} style={{ marginTop: 32 }}>
        指标库
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        本业务线共定义 <b>{indicators.length}</b> 个 KPI,数据源{" "}
        <code>mart_retail_leasing.fct_retail_leasing</code>。
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
        <EmptyState title="指标库为空" />
      )}
    </div>
  );
}
