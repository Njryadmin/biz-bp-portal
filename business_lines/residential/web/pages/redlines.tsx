// business_lines/residential/web/pages/redlines.tsx
// Residential line — three red lines (三道红线) radar.

"use client";

import { useEffect, useState } from "react";
import { Card, Col, Row, Space, Typography, Tag, Spin, Statistic, Table } from "antd";
import { UniversalChart } from "@fin-bp/ui";
import type { ColumnsType } from "antd/es/table";

const { Title, Paragraph, Text } = Typography;

interface RedlinesPayload {
  project_id: string;
  project_name: string;
  short_term_debt_yi: number;
  long_term_debt_yi: number;
  cash_yi: number;
  total_assets_yi: number;
  total_liabilities_yi: number;
  shareholders_equity_yi: number;
  asset_liability_ratio: number;
  net_debt_ratio: number;
  cash_to_short_debt: number;
  thresholds: { asset_liability_ratio: number; net_debt_ratio: number; cash_to_short_debt: number };
  status:      { asset_liability_ratio: "green" | "red"; net_debt_ratio: "green" | "red"; cash_to_short_debt: "green" | "red" };
}

const FALLBACK: RedlinesPayload = {
  project_id: "PRJ-001",
  project_name: "上海·绿城黄浦江",
  short_term_debt_yi: 8.2,
  long_term_debt_yi: 32.5,
  cash_yi: 11.8,
  total_assets_yi: 105.0,
  total_liabilities_yi: 68.0,
  shareholders_equity_yi: 37.0,
  asset_liability_ratio: 0.648,
  net_debt_ratio: 0.781,
  cash_to_short_debt: 1.439,
  thresholds: { asset_liability_ratio: 0.70, net_debt_ratio: 1.00, cash_to_short_debt: 1.00 },
  status:      { asset_liability_ratio: "green", net_debt_ratio: "green", cash_to_short_debt: "green" },
};

const PROJECT_OPTIONS = [
  { value: "PRJ-001", label: "上海·绿城黄浦江" },
  { value: "PRJ-002", label: "北京·万科海淀" },
  { value: "PRJ-003", label: "深圳·华润前海" },
  { value: "PRJ-004", label: "杭州·龙湖滨江" },
  { value: "PRJ-005", label: "成都·保利天府" },
  { value: "PRJ-006", label: "广州·中海天河" },
  { value: "PRJ-007", label: "南京·金地江宁" },
  { value: "PRJ-008", label: "苏州·金地工业园" },
];

export default function RedlinesPage() {
  const [projectId, setProjectId] = useState<string>("PRJ-001");
  const [data, setData] = useState<RedlinesPayload>(FALLBACK);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const r = await fetch(`${base}/api/lines/residential/projects/${projectId}/redlines`, { cache: "no-store" });
        if (alive && r.ok) {
          const j = await r.json();
          setData({ ...FALLBACK, ...j });
        }
      } catch {
        /* keep fallback */
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [projectId]);

  // 雷达图：当前值 vs 阈值（标准化为 % 或倍数）
  const radarOption = {
    tooltip: { trigger: "item" },
    legend: { data: ["当前值", "监管阈值"] },
    radar: {
      indicator: [
        { name: "资产负债率(%)",  max: 100 },
        { name: "净负债率(%)",    max: 150 },
        { name: "现金短债比(x×100)", max: 200 },
      ],
      radius: "65%",
    },
    series: [
      {
        type: "radar",
        data: [
          {
            name: "当前值",
            value: [
              Number((data.asset_liability_ratio * 100).toFixed(2)),
              Number((data.net_debt_ratio * 100).toFixed(2)),
              Number((data.cash_to_short_debt * 100).toFixed(2)),
            ],
            areaStyle: { color: "rgba(22,119,255,0.35)" },
            lineStyle:  { color: "#1677ff" },
            itemStyle:  { color: "#1677ff" },
          },
          {
            name: "监管阈值",
            value: [
              data.thresholds.asset_liability_ratio * 100,
              data.thresholds.net_debt_ratio * 100,
              data.thresholds.cash_to_short_debt * 100,
            ],
            areaStyle: { color: "rgba(245,34,45,0.15)" },
            lineStyle: { color: "#f5222d", type: "dashed" },
            itemStyle: { color: "#f5222d" },
          },
        ],
      },
    ],
  };

  const tableCols: ColumnsType<{ k: string; v: string; s: "green" | "red" }> = [
    { title: "指标",       dataIndex: "k", key: "k", width: 160 },
    { title: "当前值",     dataIndex: "v", key: "v", width: 160 },
    { title: "监管状态",   dataIndex: "s", key: "s",
      render: (s: "green" | "red") => <Tag color={s === "green" ? "green" : "red"}>{s === "green" ? "达标" : "踩线"}</Tag> },
  ];

  const tableData = [
    { k: "资产负债率",  v: `${(data.asset_liability_ratio * 100).toFixed(2)}%`,      s: data.status.asset_liability_ratio, threshold: `≤ ${(data.thresholds.asset_liability_ratio * 100).toFixed(0)}%` },
    { k: "净负债率",    v: `${(data.net_debt_ratio * 100).toFixed(2)}%`,            s: data.status.net_debt_ratio,           threshold: `≤ ${(data.thresholds.net_debt_ratio * 100).toFixed(0)}%` },
    { k: "现金短债比",  v: `${data.cash_to_short_debt.toFixed(2)}x`,                 s: data.status.cash_to_short_debt,       threshold: `≥ ${data.thresholds.cash_to_short_debt.toFixed(2)}x` },
  ];
  const tableCols2: ColumnsType<typeof tableData[number]> = [
    ...tableCols,
    { title: "监管阈值", dataIndex: "threshold", key: "threshold" },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>三道红线监控</Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          资产负债率 ≤ 70% / 净负债率 ≤ 100% / 现金短债比 ≥ 1.0x
        </Paragraph>
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space>
          <Text strong>项目</Text>
          <select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            style={{ padding: "4px 12px", border: "1px solid #d9d9d9", borderRadius: 6, minWidth: 220 }}
          >
            {PROJECT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Space>
      </Card>

      {loading && <Spin style={{ marginBottom: 12 }} />}

      <Row gutter={[16, 16]}>
        <Col xs={24} md={12}>
          <Card title="三道红线雷达图" size="small">
            <UniversalChart option={radarOption} style={{ height: 360 }} />
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card title="指标明细" size="small">
            <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
              <Col span={8}>
                <Statistic title="短债 (亿)"   value={data.short_term_debt_yi}     precision={2} suffix="亿" />
              </Col>
              <Col span={8}>
                <Statistic title="长债 (亿)"   value={data.long_term_debt_yi}      precision={2} suffix="亿" />
              </Col>
              <Col span={8}>
                <Statistic title="货币资金 (亿)" value={data.cash_yi}              precision={2} suffix="亿" />
              </Col>
            </Row>
            <Table
              size="small"
              rowKey={(r) => `${r.k}-${r.v}`}
              dataSource={tableData}
              columns={tableCols2}
              pagination={false}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
