// business_lines/retail/web/pages/noi.tsx
// NOI 瀑布图:Potential Gross -> Vacancy -> EGR -> OpEx -> NOI.
// 数据来源: GET /api/lines/retail/properties/{id}/noi-waterfall

"use client";

import { useEffect, useMemo, useState } from "react";
import { Col, Row, Select, Skeleton, Space, Typography } from "antd";
import {
  EmptyState,
  UniversalChart,
  UniversalKpiCard,
} from "@fin-bp/ui";

const { Title, Paragraph } = Typography;

interface WaterfallItem {
  step: string;
  value_wan: number;
  type: "start" | "subtract" | "subtotal" | "end";
}

interface WaterfallResponse {
  property_id: string;
  property_name: string;
  period: string;
  items: WaterfallItem[];
  noi_margin: number;
  implied_vacancy: number;
}

interface Property {
  property_id: string;
  name: string;
  city: string;
  format: string;
}

const API_BASE =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

const TYPE_COLOR: Record<WaterfallItem["type"], string> = {
  start: "#1677ff",
  subtract: "#cf1322",
  subtotal: "#fa8c16",
  end: "#3f8600",
};

export default function RetailNoiPage() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [propertyId, setPropertyId] = useState<string | null>(null);
  const [waterfall, setWaterfall] = useState<WaterfallResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/lines/retail/properties`)
      .then((r) => r.json())
      .then((res) => {
        if (cancelled) return;
        const items: Property[] = res.items ?? [];
        setProperties(items);
        if (items.length > 0) setPropertyId(items[0].property_id);
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
      `${API_BASE}/api/lines/retail/properties/${propertyId}/noi-waterfall`,
    )
      .then((r) => r.json())
      .then((res) => {
        if (!cancelled) setWaterfall(res);
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [propertyId]);

  const option = useMemo(() => {
    if (!waterfall) return {};
    const labels = waterfall.items.map((i) => i.step);
    const values = waterfall.items.map((i) => i.value_wan);
    const colors = waterfall.items.map((i) => TYPE_COLOR[i.type]);
    return {
      tooltip: { trigger: "axis" },
      grid: { left: 64, right: 24, top: 24, bottom: 48 },
      xAxis: { type: "category", data: labels, axisLabel: { fontSize: 11 } },
      yAxis: { type: "value", name: "金额 (万元)" },
      series: [
        {
          type: "bar",
          data: values.map((v, idx) => ({
            value: v,
            itemStyle: { color: colors[idx] },
          })),
          label: {
            show: true,
            position: "top",
            formatter: (p: { value: number }) => p.value.toLocaleString(),
            fontSize: 11,
          },
        },
      ],
    };
  }, [waterfall]);

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <EmptyState title="加载失败" description={error} />
      </div>
    );
  }

  const finalNoi = waterfall?.items.find((i) => i.type === "end")?.value_wan;
  const egr = waterfall?.items.find((i) => i.type === "subtotal")?.value_wan;

  return (
    <div style={{ padding: 24 }}>
      <Title level={3} style={{ marginBottom: 4 }}>
        NOI 瀑布
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        毛租金 → 扣除空置/坏账 → 有效毛收入 → 扣运营成本 → NOI 的逐项分解。
      </Paragraph>

      <Space style={{ marginBottom: 16 }}>
        <span>选择物业:</span>
        <Select
          style={{ minWidth: 280 }}
          value={propertyId ?? undefined}
          onChange={(v) => setPropertyId(v)}
          loading={properties.length === 0}
          options={properties.map((p) => ({
            value: p.property_id,
            label: `${p.name} · ${p.city} · ${p.format}`,
          }))}
        />
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="毛租金 (Potential)"
            value={
              waterfall?.items.find((i) => i.type === "start")?.value_wan ?? null
            }
            format="currency"
            unit="万元"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="有效毛收入 (EGR)"
            value={egr ?? null}
            format="currency"
            unit="万元"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="NOI"
            value={finalNoi ?? null}
            format="currency"
            unit="万元"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="NOI 率"
            value={waterfall?.noi_margin ?? null}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
      </Row>

      <Title level={4} style={{ marginTop: 32 }}>
        瀑布图
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 400 }} />
      ) : (
        <UniversalChart option={option} style={{ height: 400 }} />
      )}

      <Paragraph type="secondary" style={{ marginTop: 16, fontSize: 12 }}>
        数据周期:{waterfall?.period ?? "—"} · 隐含空置率:
        {((waterfall?.implied_vacancy ?? 0) * 100).toFixed(2)}%
      </Paragraph>
    </div>
  );
}
