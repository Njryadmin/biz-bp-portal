// business_lines/retail-leasing/web/pages/leasing-kpi.tsx
// 租赁 KPI 仪表盘:
//   - 8 个核心 KPI 卡(对应 indicators.yaml)
//   - 佣金收入 vs 续约率散点
//   - 业主 × 续约率柱状

"use client";

import { useEffect, useMemo, useState } from "react";
import { Col, Row, Skeleton, Typography } from "antd";
import {
  EmptyState,
  UniversalChart,
  UniversalKpiCard,
} from "@fin-bp/ui";

const { Title, Paragraph } = Typography;

interface PropertyHeadline {
  property_id: string;
  name: string;
  city: string;
  owner: string;
  deal_rent_yuan_per_sqm_per_month: number;
  benchmark_gap_pct: number;
  commission_revenue_wan: number;
  renewal_rate: number;
  brand_entry_rate: number;
  owner_vacancy_days: number;
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

export default function LeasingKpiPage() {
  const [data, setData] = useState<PropertiesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/lines/retail-leasing/properties`)
      .then((r) => r.json())
      .then((res) => !cancelled && setData(res))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const portfolioKpis = useMemo(() => {
    const items = data?.items ?? [];
    if (items.length === 0) {
      return {
        avgOccupancy: 0,
        avgDealRent: 0,
        avgBenchmarkGap: 0,
        avgVacancyDays: 0,
        totalReports: 0,
        avgBrandEntry: 0,
        avgRenewal: 0,
        totalCommission: 0,
      };
    }
    const n = items.length;
    return {
      avgOccupancy:
        items.reduce((s, p) => s + p.headline_kpis.occupancy_rate, 0) / n,
      avgDealRent:
        items.reduce((s, p) => s + p.headline_kpis.avg_deal_rent, 0) / n,
      avgBenchmarkGap:
        items.reduce((s, p) => s + p.headline_kpis.benchmark_gap_pct, 0) / n,
      avgVacancyDays:
        items.reduce((s, p) => s + p.headline_kpis.owner_vacancy_days, 0) / n,
      totalReports: items.reduce(
        (s, p) => s + p.headline_kpis.quarterly_market_reports,
        0
      ),
      avgBrandEntry:
        items.reduce((s, p) => s + p.headline_kpis.brand_entry_rate, 0) / n,
      avgRenewal:
        items.reduce((s, p) => s + p.headline_kpis.renewal_rate, 0) / n,
      totalCommission: items.reduce(
        (s, p) => s + p.headline_kpis.commission_revenue,
        0
      ),
    };
  }, [data]);

  const renewalChartOption = useMemo(() => {
    const items = data?.items ?? [];
    return {
      tooltip: { trigger: "axis" },
      grid: { left: 56, right: 24, top: 24, bottom: 80 },
      xAxis: {
        type: "category",
        data: items.map((p) => p.owner),
        axisLabel: { rotate: 30, fontSize: 11 },
      },
      yAxis: {
        type: "value",
        name: "续约率",
        min: 0.4,
        max: 1.0,
        axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      },
      series: [
        {
          type: "bar",
          data: items.map((p) => Number(p.renewal_rate.toFixed(4))),
          itemStyle: { color: "#52c41a" },
          label: {
            show: true,
            position: "top",
            formatter: (p: { value: number }) =>
              `${(p.value * 100).toFixed(0)}%`,
            fontSize: 11,
          },
        },
      ],
    };
  }, [data]);

  const commissionChartOption = useMemo(() => {
    const items = data?.items ?? [];
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["佣金收入 (万元)", "续约率"] },
      grid: { left: 64, right: 64, top: 32, bottom: 80 },
      xAxis: {
        type: "category",
        data: items.map((p) => p.name),
        axisLabel: { rotate: 25, fontSize: 11 },
      },
      yAxis: [
        { type: "value", name: "佣金收入 (万元)" },
        {
          type: "value",
          name: "续约率",
          min: 0.4,
          max: 1.0,
          position: "right",
          axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
        },
      ],
      series: [
        {
          name: "佣金收入 (万元)",
          type: "bar",
          data: items.map((p) => p.commission_revenue_wan),
          itemStyle: { color: "#fa8c16" },
        },
        {
          name: "续约率",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          data: items.map((p) => Number(p.renewal_rate.toFixed(4))),
          itemStyle: { color: "#1677ff" },
        },
      ],
    };
  }, [data]);

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
        租赁 KPI 仪表盘
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        8 个核心 KPI,逐项对应 indicators.yaml。所有数据源自本期 <b>{data?.count ?? 0}</b> 个在管商铺。
      </Paragraph>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="商铺出租率"
            value={portfolioKpis.avgOccupancy}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="平均成交租金"
            value={portfolioKpis.avgDealRent}
            format="currency"
            unit="元/㎡/月"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="竞品基准对标差"
            value={portfolioKpis.avgBenchmarkGap}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="业主空置期"
            value={portfolioKpis.avgVacancyDays}
            format="number"
            unit="天"
            loading={loading}
            precision={0}
          />
        </Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="季度市场报告"
            value={portfolioKpis.totalReports}
            format="number"
            unit="份"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="品牌入驻率"
            value={portfolioKpis.avgBrandEntry}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="续约率"
            value={portfolioKpis.avgRenewal}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="佣金收入"
            value={portfolioKpis.totalCommission}
            format="currency"
            unit="万元"
            loading={loading}
            precision={1}
          />
        </Col>
      </Row>

      <Title level={4} style={{ marginTop: 32 }}>
        续约率 (按业主)
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 320 }} />
      ) : (
        <UniversalChart option={renewalChartOption} style={{ height: 320 }} />
      )}

      <Title level={4} style={{ marginTop: 32 }}>
        佣金收入 vs 续约率
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 360 }} />
      ) : (
        <UniversalChart
          option={commissionChartOption}
          style={{ height: 360 }}
        />
      )}
    </div>
  );
}
