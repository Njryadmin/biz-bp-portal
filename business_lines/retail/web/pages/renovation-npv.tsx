// business_lines/retail/web/pages/renovation-npv.tsx
// 调改 NPV 测算:维持 vs 调改两档对比.
// 数据来源: GET /api/lines/retail/properties/{id}/renovation-npv

"use client";

import { useEffect, useMemo, useState } from "react";
import { Col, InputNumber, Row, Select, Skeleton, Space, Typography } from "antd";
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

interface ScenarioResult {
  scenario: "maintain" | "renovate";
  capex_wan: number;
  annual_noi_year1_wan: number;
  noi_growth: number;
  npv_wan: number;
  irr: number | null;
}

interface NpvResponse {
  property_id: string;
  property_name: string;
  horizon_years: number;
  discount_rate: number;
  terminal_cap_rate: number;
  maintain: ScenarioResult;
  renovate: ScenarioResult;
  delta: {
    npv_wan: number;
    delta_label: string;
  };
}

const API_BASE =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

export default function RetailRenovationNpvPage() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [propertyId, setPropertyId] = useState<string | null>(null);
  const [horizon, setHorizon] = useState<number>(10);
  const [discountRate, setDiscountRate] = useState<number>(0.08);
  const [npv, setNpv] = useState<NpvResponse | null>(null);
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
    const url = `${API_BASE}/api/lines/retail/properties/${propertyId}/renovation-npv?horizon=${horizon}&discount_rate=${discountRate}`;
    fetch(url)
      .then((r) => r.json())
      .then((res) => !cancelled && setNpv(res))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [propertyId, horizon, discountRate]);

  const compareOption = useMemo(() => {
    if (!npv) return {};
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["NPV (万元)", "IRR"] },
      grid: { left: 64, right: 64, top: 36, bottom: 32 },
      xAxis: {
        type: "category",
        data: [npv.maintain.scenario, npv.renovate.scenario],
      },
      yAxis: [
        { type: "value", name: "NPV (万元)", position: "left" },
        {
          type: "value",
          name: "IRR",
          position: "right",
          axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
        },
      ],
      series: [
        {
          name: "NPV (万元)",
          type: "bar",
          data: [npv.maintain.npv_wan, npv.renovate.npv_wan],
          itemStyle: { color: "#1677ff" },
          label: { show: true, position: "top", fontSize: 11 },
        },
        {
          name: "IRR",
          type: "line",
          yAxisIndex: 1,
          data: [
            { value: npv.maintain.irr ?? 0 },
            { value: npv.renovate.irr ?? 0 },
          ],
          itemStyle: { color: "#fa8c16" },
          label: {
            show: true,
            formatter: (p: { value: number }) =>
              `${(p.value * 100).toFixed(1)}%`,
            fontSize: 11,
          },
        },
      ],
    };
  }, [npv]);

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
        调改 NPV
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        维持 vs 调改两档方案的 NPV/IRR 对比。资本支出按 600 元/㎡ 一次性投入,
        调改后 NOI 首年提升 12%, 后续递增率在基础值上 +1.5%。
      </Paragraph>

      <Space size="large" wrap style={{ marginBottom: 16 }}>
        <Space>
          <span>物业:</span>
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
        <Space>
          <span>持有期 (年):</span>
          <InputNumber
            min={3}
            max={20}
            value={horizon}
            onChange={(v) => v != null && setHorizon(v)}
          />
        </Space>
        <Space>
          <span>折现率:</span>
          <InputNumber
            min={0.02}
            max={0.25}
            step={0.005}
            value={discountRate}
            onChange={(v) => v != null && setDiscountRate(v)}
            formatter={(v) => `${((v ?? 0) * 100).toFixed(1)}%`}
            parser={(v) => (Number(String(v).replace("%", "")) || 0) / 100}
          />
        </Space>
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="维持 NPV"
            value={npv?.maintain.npv_wan ?? null}
            format="currency"
            unit="万元"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="调改 NPV"
            value={npv?.renovate.npv_wan ?? null}
            format="currency"
            unit="万元"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="NPV 增量"
            value={npv?.delta.npv_wan ?? null}
            format="currency"
            unit="万元"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <UniversalKpiCard
            title="维持 IRR"
            value={npv?.maintain.irr ?? null}
            format="percent"
            loading={loading}
            precision={2}
          />
        </Col>
      </Row>

      <Title level={4} style={{ marginTop: 32 }}>
        两档方案对比
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 360 }} />
      ) : (
        <UniversalChart option={compareOption} style={{ height: 360 }} />
      )}

      {npv && (
        <Paragraph
          type={npv.delta.npv_wan > 0 ? "success" : "warning"}
          style={{ marginTop: 16, fontSize: 14 }}
        >
          <b>决策建议:</b> {npv.delta.delta_label} (Δ NPV ={" "}
          {npv.delta.npv_wan.toLocaleString()} 万元)
        </Paragraph>
      )}
    </div>
  );
}
