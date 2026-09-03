// business_lines/residential/web/pages/dedup-forecast.tsx
// Residential line — dedup (去化) forecast with confidence band.

"use client";

import { useEffect, useState } from "react";
import { Card, Col, Row, Space, Typography, Spin, Statistic, Tag } from "antd";
import { UniversalChart } from "@fin-bp/ui";

const { Title, Paragraph, Text } = Typography;

interface DedupPayload {
  project_id: string;
  project_name: string;
  history: number[];
  history_months: string[];
  forecast_median: number[];
  forecast_lower: number[];
  forecast_upper: number[];
  forecast_months: string[];
}

const FALLBACK: DedupPayload = {
  project_id: "PRJ-001",
  project_name: "上海·绿城黄浦江",
  history:        [0.04, 0.05, 0.06, 0.07, 0.05, 0.04, 0.06, 0.08, 0.07, 0.05, 0.04, 0.03],
  history_months: ["M-11", "M-10", "M-9", "M-8", "M-7", "M-6", "M-5", "M-4", "M-3", "M-2", "M-1", "M"],
  forecast_median: [0.05, 0.06, 0.07, 0.06, 0.05, 0.05, 0.04, 0.04, 0.04, 0.03, 0.03, 0.03],
  forecast_lower:  [0.03, 0.04, 0.05, 0.04, 0.03, 0.03, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01],
  forecast_upper:  [0.07, 0.08, 0.09, 0.08, 0.07, 0.07, 0.06, 0.06, 0.06, 0.05, 0.05, 0.05],
  forecast_months: ["M+1", "M+2", "M+3", "M+4", "M+5", "M+6", "M+7", "M+8", "M+9", "M+10", "M+11", "M+12"],
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

export default function DedupForecastPage() {
  const [projectId, setProjectId] = useState<string>("PRJ-001");
  const [data, setData] = useState<DedupPayload>(FALLBACK);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const r = await fetch(`${base}/api/lines/residential/projects/${projectId}/dedup-forecast`, { cache: "no-store" });
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

  // x 轴：history + forecast 拼接
  const allMonths = [...data.history_months, ...data.forecast_months];
  const historyPadded = [...data.history, ...new Array(data.forecast_months.length).fill(null)];
  const medianPadded  = [...new Array(data.history_months.length).fill(null), ...data.forecast_median];
  const lowerPadded   = [...new Array(data.history_months.length).fill(null), ...data.forecast_lower];
  const upperPadded   = [...new Array(data.history_months.length).fill(null), ...data.forecast_upper];

  const chartOption = {
    tooltip: { trigger: "axis" },
    legend: { data: ["历史", "预测中位数", "80% 置信区间"] },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: { type: "category", data: allMonths },
    yAxis: { type: "value", name: "去化率", axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
    series: [
      { name: "历史",         type: "line", data: historyPadded, smooth: true, itemStyle: { color: "#8c8c8c" } },
      { name: "80% 置信区间", type: "line", data: upperPadded,    smooth: true, lineStyle: { type: "dotted", color: "#52c41a" }, itemStyle: { color: "#52c41a" }, stack: "ci-upper" },
      { name: "80% 置信区间", type: "line", data: lowerPadded,    smooth: true, lineStyle: { type: "dotted", color: "#52c41a" }, itemStyle: { color: "#52c41a" } },
      { name: "预测中位数",   type: "line", data: medianPadded,   smooth: true, lineStyle: { width: 3 }, itemStyle: { color: "#1677ff" } },
    ],
  };

  const totalForecast = data.forecast_median.reduce((a, b) => a + b, 0);
  const lastHistory   = data.history[data.history.length - 1] ?? 0;
  const trendUp = (data.forecast_median[0] ?? 0) >= lastHistory;

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>去化预测</Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          历史 12 个月 + 未来 12 个月去化率点预测及 80% 置信区间
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
          <Tag color={trendUp ? "green" : "orange"}>下一月趋势: {trendUp ? "↑" : "↓"} {(data.forecast_median[0] ?? 0).toFixed(4)}</Tag>
        </Space>
      </Card>

      {loading && <Spin style={{ marginBottom: 12 }} />}

      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}><Card size="small"><Statistic title="当月去化率"  value={lastHistory}         precision={4} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="预测 1 月去化" value={data.forecast_median[0] ?? 0} precision={4} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="未来 12 月累计" value={totalForecast}      precision={4} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="区间宽度均值" value={
          (data.forecast_upper.reduce((a, b) => a + b, 0) - data.forecast_lower.reduce((a, b) => a + b, 0)) / Math.max(data.forecast_upper.length, 1)
        } precision={4} /></Card></Col>
      </Row>

      <Card title="去化率历史 + 预测" size="small">
        <UniversalChart option={chartOption} style={{ height: 380 }} />
      </Card>
    </div>
  );
}
