// business_lines/retail/web/pages/brand-mix.tsx
// 品牌组合气泡图 + 多样性指数 + 业态级面积/租金分布.

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

interface BrandMixResponse {
  property_id: string;
  property_name: string;
  total_brands: number;
  sampled_leases: number;
  categories: Array<{
    category: string;
    brand_count: number;
    area_share: number;
    avg_rent_yuan_per_sqm_per_month: number;
  }>;
  diversity_index: number;
  top_brands: string[];
}

const API_BASE =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

export default function RetailBrandMixPage() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [propertyId, setPropertyId] = useState<string | null>(null);
  const [mix, setMix] = useState<BrandMixResponse | null>(null);
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
    fetch(`${API_BASE}/api/lines/retail/properties/${propertyId}/brand-mix`)
      .then((r) => r.json())
      .then((res) => !cancelled && setMix(res))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [propertyId]);

  const bubbleOption = useMemo(() => {
    if (!mix) return {};
    return {
      tooltip: {
        trigger: "item",
        formatter: (p: {
          data: [string, number, number, number];
        }) =>
          `<b>${p.data[0]}</b><br/>面积占比: ${(p.data[1] * 100).toFixed(1)}%<br/>平均租金: ${p.data[2]} 元/㎡/月<br/>品牌数: ${p.data[3]}`,
      },
      grid: { left: 64, right: 24, top: 24, bottom: 64 },
      xAxis: {
        type: "value",
        name: "面积占比",
        axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      },
      yAxis: { type: "value", name: "平均租金 (元/㎡/月)" },
      series: [
        {
          type: "scatter",
          symbolSize: (d: [string, number, number, number]) =>
            Math.max(20, d[3] * 4),
          data: mix.categories.map((c) => [
            c.category,
            c.area_share,
            c.avg_rent_yuan_per_sqm_per_month,
            c.brand_count,
          ]),
          itemStyle: { color: "#722ed1", opacity: 0.8 },
          label: {
            show: true,
            position: "right",
            formatter: (p: { data: [string, number, number, number] }) =>
              p.data[0],
            fontSize: 11,
          },
        },
      ],
    };
  }, [mix]);

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
        品牌组合
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        业态级面积占比 × 平均租金 × 品牌数。多样性指数 = Shannon 熵 / ln(业态数),
        越接近 1 越多元。
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
            title="品牌多样性指数"
            value={mix?.diversity_index ?? null}
            format="ratio"
            loading={loading}
            precision={4}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="入驻品牌数"
            value={mix?.total_brands ?? null}
            format="number"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="业态数 (采样)"
            value={mix?.sampled_leases ?? null}
            format="number"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="头部品牌"
            value={mix?.top_brands?.[0] ? 1 : 0}
            format="number"
            loading={loading}
            precision={0}
          />
        </Col>
      </Row>

      <Title level={4} style={{ marginTop: 32 }}>
        业态气泡图
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 400 }} />
      ) : (
        <UniversalChart option={bubbleOption} style={{ height: 400 }} />
      )}

      <Title level={4} style={{ marginTop: 32 }}>
        业态明细
      </Title>
      {mix && mix.categories.length > 0 ? (
        <Row gutter={[12, 12]}>
          {mix.categories.map((c) => (
            <Col key={c.category} xs={24} sm={12} md={8} lg={6}>
              <Space direction="vertical" size={2} style={{ width: "100%" }}>
                <Typography.Text strong>{c.category}</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  面积占比 {(c.area_share * 100).toFixed(1)}% · 品牌数{" "}
                  {c.brand_count} · 平均租金{" "}
                  {c.avg_rent_yuan_per_sqm_per_month.toFixed(0)} 元/㎡/月
                </Typography.Text>
              </Space>
            </Col>
          ))}
        </Row>
      ) : (
        !loading && <EmptyState title="无业态数据" />
      )}
    </div>
  );
}
