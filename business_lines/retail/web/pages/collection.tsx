// business_lines/retail/web/pages/collection.tsx
// 收缴率趋势:12 个月滚动 + 当前值.

"use client";

import { useEffect, useMemo, useState } from "react";
import { Col, Row, Select, Skeleton, Space, Typography } from "antd";
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
}

interface CollectionResponse {
  property_id: string;
  property_name: string;
  current_rate: number;
  average_rate_12m: number;
  worst_month: { month: string; rate: number };
  best_month: { month: string; rate: number };
  trend: Array<{ month: string; rate: number }>;
}

const API_BASE =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

export default function RetailCollectionPage() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [propertyId, setPropertyId] = useState<string | null>(null);
  const [data, setData] = useState<CollectionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/lines/retail/properties`)
      .then((r) => r.json())
      .then((res) => {
        if (cancelled) return;
        setProperties(res.items ?? []);
        if ((res.items ?? []).length > 0) {
          setPropertyId(res.items[0].property_id);
        }
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!propertyId) return;
    let cancelled = false;
    setLoading(true);
    fetch(
      `${API_BASE}/api/lines/retail/properties/${propertyId}/collection-rate`,
    )
      .then((r) => r.json())
      .then((res) => !cancelled && setData(res))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [propertyId]);

  const trendOption = useMemo(() => {
    if (!data) return {};
    return {
      tooltip: {
        trigger: "axis",
        formatter: (params: Array<{ axisValue: string; value: number }>) => {
          const p = params[0];
          return `${p.axisValue}<br/>收缴率: ${(p.value * 100).toFixed(2)}%`;
        },
      },
      grid: { left: 64, right: 24, top: 24, bottom: 48 },
      xAxis: {
        type: "category",
        data: data.trend.map((t) => t.month),
        axisLabel: { fontSize: 11 },
      },
      yAxis: {
        type: "value",
        name: "收缴率",
        min: 0.85,
        max: 1.0,
        axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      },
      series: [
        {
          type: "line",
          smooth: true,
          data: data.trend.map((t) => t.rate),
          areaStyle: { color: "rgba(22,119,255,0.18)" },
          itemStyle: { color: "#1677ff" },
          lineStyle: { width: 2 },
          markLine: {
            data: [{ yAxis: 0.95, name: "健康线 95%" }],
            lineStyle: { color: "#fa8c16", type: "dashed" },
          },
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
        收缴率
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        当期应收租金中实际收回的比例。健康线 95%,低于此值需关注租户履约与现金流。
      </Paragraph>

      <Space style={{ marginBottom: 16 }}>
        <span>选择物业:</span>
        <Select
          style={{ minWidth: 280 }}
          value={propertyId ?? undefined}
          onChange={(v) => setPropertyId(v)}
          options={properties.map((p) => ({
            value: p.property_id,
            label: `${p.name} · ${p.city}`,
          }))}
        />
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="当前收缴率"
            value={data?.current_rate ?? null}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="12 月平均"
            value={data?.average_rate_12m ?? null}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="最佳月份"
            value={data?.best_month.rate ?? null}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="最差月份"
            value={data?.worst_month.rate ?? null}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
      </Row>

      <Title level={4} style={{ marginTop: 32 }}>
        12 个月趋势
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 360 }} />
      ) : (
        <UniversalChart option={trendOption} style={{ height: 360 }} />
      )}
    </div>
  );
}
