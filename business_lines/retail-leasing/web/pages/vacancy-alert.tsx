// business_lines/retail-leasing/web/pages/vacancy-alert.tsx
// 业主空置预警:
//   - 阈值可调 (?threshold_days 走 query 参数)
//   - 高/中/低 严重度分组
//   - 各业主的最坏空置期 + 关联商铺列表

"use client";

import { useEffect, useMemo, useState } from "react";
import { Alert, Col, Row, Skeleton, Space, Table, Typography } from "antd";
import {
  EmptyState,
  UniversalChart,
  UniversalKpiCard,
} from "@fin-bp/ui";

const { Title, Paragraph } = Typography;

interface AlertItem {
  owner: string;
  severity: "high" | "medium" | "low";
  max_vacancy_days: number;
  worst_property: string;
  property_count: number;
  properties: Array<{
    property_id: string;
    property_name: string;
    city: string;
    owner_vacancy_days: number;
    deal_rent: number;
  }>;
}

interface VacancyResponse {
  line_id: string;
  threshold_days: number;
  alert_count: number;
  alerts: AlertItem[];
}

const API_BASE =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

export default function VacancyAlertPage() {
  const [data, setData] = useState<VacancyResponse | null>(null);
  const [threshold, setThreshold] = useState<number>(60);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(
      `${API_BASE}/api/lines/retail-leasing/vacancy-alerts?threshold_days=${threshold}`
    )
      .then((r) => r.json())
      .then((res) => !cancelled && setData(res))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [threshold]);

  const severityCounts = useMemo(() => {
    const out = { high: 0, medium: 0, low: 0 };
    (data?.alerts ?? []).forEach((a) => {
      out[a.severity] += 1;
    });
    return out;
  }, [data]);

  const barOption = useMemo(() => {
    const alerts = data?.alerts ?? [];
    return {
      tooltip: { trigger: "axis" },
      grid: { left: 56, right: 24, top: 24, bottom: 80 },
      xAxis: {
        type: "category",
        data: alerts.map((a) => a.owner),
        axisLabel: { rotate: 30, fontSize: 11 },
      },
      yAxis: { type: "value", name: "最大空置期 (天)" },
      series: [
        {
          type: "bar",
          data: alerts.map((a) => a.max_vacancy_days),
          itemStyle: {
            color: (p: { dataIndex: number }) => {
              const a = alerts[p.dataIndex];
              if (!a) return "#1677ff";
              if (a.severity === "high") return "#cf1322";
              if (a.severity === "medium") return "#fa8c16";
              return "#52c41a";
            },
          },
          label: { show: true, position: "top", fontSize: 11 },
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
        业主空置预警
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        业主在商铺上一个租约结束到新签之间的空置天数,超过阈值则触发预警。
        阈值当前为 <b>{threshold}</b> 天,可在下方调整。
      </Paragraph>

      <Space style={{ marginBottom: 16 }}>
        <span>阈值 (天):</span>
        <ThresholdSelector value={threshold} onChange={setThreshold} />
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <UniversalKpiCard
            title="高风险业主"
            value={severityCounts.high}
            format="number"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={8}>
          <UniversalKpiCard
            title="中风险业主"
            value={severityCounts.medium}
            format="number"
            loading={loading}
            precision={0}
          />
        </Col>
        <Col xs={24} sm={8}>
          <UniversalKpiCard
            title="预警合计"
            value={data?.alert_count ?? 0}
            format="number"
            loading={loading}
            precision={0}
          />
        </Col>
      </Row>

      {data && data.alert_count === 0 ? (
        <Alert
          style={{ marginTop: 24 }}
          type="success"
          showIcon
          message="当前阈值下无空置预警"
          description="所有在管商铺的业主空置期均低于阈值。"
        />
      ) : null}

      <Title level={4} style={{ marginTop: 32 }}>
        各业主最大空置期
      </Title>
      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 360 }} />
      ) : (
        <UniversalChart option={barOption} style={{ height: 360 }} />
      )}

      <Title level={4} style={{ marginTop: 32 }}>
        预警明细
      </Title>
      {(data?.alerts ?? []).length > 0 ? (
        <Table<AlertItem>
          rowKey="owner"
          dataSource={data?.alerts ?? []}
          pagination={false}
          size="small"
          columns={[
            { title: "业主", dataIndex: "owner", key: "owner", width: 200 },
            {
              title: "严重度",
              dataIndex: "severity",
              key: "severity",
              width: 100,
              render: (v: string) => {
                const color =
                  v === "high" ? "red" : v === "medium" ? "orange" : "green";
                const label =
                  v === "high" ? "高" : v === "medium" ? "中" : "低";
                return <span style={{ color }}>{label}</span>;
              },
            },
            {
              title: "最大空置期 (天)",
              dataIndex: "max_vacancy_days",
              key: "max",
              width: 130,
            },
            { title: "最差物业", dataIndex: "worst_property", key: "worst" },
            {
              title: "关联商铺数",
              dataIndex: "property_count",
              key: "pcount",
              width: 110,
            },
          ]}
          expandable={{
            expandedRowRender: (record) => (
              <Table
                size="small"
                rowKey="property_id"
                dataSource={record.properties}
                pagination={false}
                columns={[
                  { title: "ID", dataIndex: "property_id", width: 240 },
                  { title: "商铺", dataIndex: "property_name" },
                  { title: "城市", dataIndex: "city", width: 100 },
                  {
                    title: "空置期 (天)",
                    dataIndex: "owner_vacancy_days",
                    width: 110,
                  },
                  {
                    title: "成交租金",
                    dataIndex: "deal_rent",
                    width: 120,
                    render: (v: number) => `${v} 元/㎡/月`,
                  },
                ]}
              />
            ),
          }}
        />
      ) : (
        <EmptyState
          title={loading ? "加载中..." : "无预警"}
          description="当前阈值下所有业主的空置期均在合理范围。"
        />
      )}
    </div>
  );
}

function ThresholdSelector(props: {
  value: number;
  onChange: (v: number) => void;
}) {
  // Simple preset selector — keeps the page dependency-free.
  const opts = [30, 45, 60, 90, 120];
  return (
    <Space>
      {opts.map((v) => (
        <a
          key={v}
          onClick={(e) => {
            e.preventDefault();
            props.onChange(v);
          }}
          style={{
            padding: "2px 10px",
            border: "1px solid #d9d9d9",
            borderRadius: 4,
            background: props.value === v ? "#1677ff" : "#fff",
            color: props.value === v ? "#fff" : "#1677ff",
            cursor: "pointer",
            fontSize: 12,
          }}
        >
          {v} 天
        </a>
      ))}
    </Space>
  );
}
