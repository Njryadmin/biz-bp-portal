// apps/web/app/(dashboard)/forecast/page.tsx
//
// Rolling Forecast Engine — universal across business lines.
//
// Layout:
//   ┌────────────┬────────────────────────────────────────────┐
//   │  Params    │  Historical + forecast line chart (top)    │
//   │  panel     │   - shaded 95% confidence band              │
//   │  (left)    │  MAPE / Bias gauge (mid)                    │
//   │  320px     │  Attribution table (bottom)                 │
//   └────────────┴────────────────────────────────────────────┘
//
// Data flow:
//   1. On mount, GET /api/forecast/profiles → fills dropdowns.
//   2. User clicks [Run Forecast] → POST /api/forecast/run →
//      render chart + metrics + attribution.
//   3. include_attribution checkbox adds the attribution table.

"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Form,
  InputNumber,
  Row,
  Select,
  Skeleton,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import { LineChartOutlined, ReloadOutlined } from "@ant-design/icons";
import { EmptyState, UniversalChart } from "@biz-bp/ui";

const { Title, Paragraph, Text } = Typography;

interface SeriesDef {
  indicator_id: string;
  name: string;
  frequency: string;
  method: string;
  horizon_months: number;
  historical_periods: number;
}

interface AttributionDef {
  id: string;
  name: string;
  driver_count: number;
}

interface Profile {
  line_id: string;
  line_name: string;
  series_count: number;
  attribution_count: number;
  series: SeriesDef[];
  attribution: AttributionDef[];
}

interface ProfilesResponse {
  count: number;
  profiles: Profile[];
}

interface ForecastPoint {
  period: string;
  point: number;
  lower: number;
  upper: number;
  is_actual: boolean;
}

interface AttributionRow {
  factor_id: string;
  factor_name: string;
  weight: number;
  top_driver: string;
}

interface ForecastResult {
  line_id: string;
  indicator_id: string;
  indicator_name: string;
  method: string;
  frequency: string;
  historical: ForecastPoint[];
  forecast: ForecastPoint[];
  mape: number | null;
  bias: number | null;
  confidence: number;
  attribution: AttributionRow[] | null;
}

function fmtPct(x: number, digits = 1): string {
  if (!Number.isFinite(x)) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}

function fmtNum(x: number, digits = 2): string {
  if (!Number.isFinite(x)) return "—";
  return x.toLocaleString("en-US", { maximumFractionDigits: digits });
}

const METHODS = [
  { value: "linear_trend", label: "线性趋势 (OLS)" },
  { value: "sma", label: "简单移动平均 (SMA)" },
  { value: "ema", label: "指数平滑 (EMA)" },
  { value: "seasonal_naive", label: "季节性朴素法" },
];

export default function ForecastPage() {
  const searchParams = useSearchParams();
  const initialLine = searchParams?.get("line") ?? null;

  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loadingProfiles, setLoadingProfiles] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);

  const [form] = Form.useForm();
  const lineId = Form.useWatch("line_id", form);
  const indicatorId = Form.useWatch("indicator_id", form);
  const method = Form.useWatch("method", form);
  const horizon = Form.useWatch("horizon_months", form);
  const includeAttribution = Form.useWatch("include_attribution", form);

  const [result, setResult] = useState<ForecastResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRun, setHasRun] = useState(false);

  // ── Load profiles on mount ──
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/forecast/profiles", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as ProfilesResponse;
        if (!cancelled) {
          setProfiles(data.profiles ?? []);
          setProfileError(null);
          // Honor ?line= query param; fall back to the first profile.
          const targetLine = initialLine
            ? data.profiles?.find((p) => p.line_id === initialLine) ?? data.profiles?.[0]
            : data.profiles?.[0];
          if (targetLine) {
            const s = targetLine.series?.[0];
            form.setFieldsValue({
              line_id: targetLine.line_id,
              indicator_id: s?.indicator_id,
              method: s?.method ?? "linear_trend",
              horizon_months: s?.horizon_months ?? 12,
              include_attribution: true,
            });
          }
        }
      } catch (e) {
        if (!cancelled) setProfileError((e as Error).message);
      } finally {
        if (!cancelled) setLoadingProfiles(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [form, initialLine]);

  // ── Profile / series selectors ──
  const profile = useMemo(
    () => profiles.find((p) => p.line_id === lineId),
    [profiles, lineId],
  );
  const series = useMemo(
    () => profile?.series.find((s) => s.indicator_id === indicatorId) ?? null,
    [profile, indicatorId],
  );

  // When the line changes, reset indicator + method + horizon defaults.
  useEffect(() => {
    if (!profile) return;
    const currentInd = form.getFieldValue("indicator_id");
    if (!profile.series.find((s) => s.indicator_id === currentInd)) {
      const s = profile.series[0];
      form.setFieldsValue({
        indicator_id: s?.indicator_id,
        method: s?.method ?? "linear_trend",
        horizon_months: s?.horizon_months ?? 12,
      });
    }
  }, [profile, form]);

  // When the indicator changes, snap method + horizon to series defaults
  // (only if the user hasn't already picked a different value).
  useEffect(() => {
    if (!series) return;
    form.setFieldsValue({
      method: series.method,
      horizon_months: series.horizon_months,
    });
  }, [series, form]);

  // ── Run forecast ──
  async function runForecast() {
    if (!profile || !indicatorId) return;
    setError(null);
    setLoading(true);
    setHasRun(true);
    try {
      const body = {
        line_id: profile.line_id,
        indicator_id: indicatorId,
        method: method || undefined,
        horizon_months: horizon || undefined,
        include_attribution: Boolean(includeAttribution),
      };
      const res = await fetch("/api/forecast/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail ?? `HTTP ${res.status}`);
      }
      const data = (await res.json()) as ForecastResult;
      setResult(data);
    } catch (e) {
      setError((e as Error).message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  // Auto-run on first profile load.
  useEffect(() => {
    if (profiles.length > 0 && !hasRun && !loading) {
      runForecast();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profiles.length]);

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          <LineChartOutlined /> 滚动预测引擎
        </Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          通用引擎 — 选择业务线、KPI 和预测方法,生成未来 12 个月滚动预测(带 95% 置信带)与归因。所有业务线共用同一套模型。
        </Paragraph>
      </Space>

      <Row gutter={16}>
        {/* ── LEFT: param panel ── */}
        <Col xs={24} md={8} lg={7} xl={6}>
          <Card
            size="small"
            title="参数面板"
            extra={
              <Button
                type="primary"
                icon={<ReloadOutlined />}
                onClick={runForecast}
                loading={loading}
                disabled={!profile || !indicatorId}
                size="small"
              >
                Run Forecast
              </Button>
            }
          >
            {loadingProfiles ? (
              <Skeleton active paragraph={{ rows: 6 }} />
            ) : profileError ? (
              <Alert type="error" message={`加载 profiles 失败: ${profileError}`} />
            ) : profiles.length === 0 ? (
              <EmptyState
                title="尚无业务线配置预测"
                description="在 business_lines/<line>/forecast.yaml 添加配置后即可使用。"
              />
            ) : (
              <Form form={form} layout="vertical" size="small">
                <Form.Item label="业务线" name="line_id">
                  <Select
                    options={profiles.map((p) => ({
                      value: p.line_id,
                      label: `${p.line_name} (${p.series_count} series)`,
                    }))}
                  />
                </Form.Item>
                <Form.Item label="KPI 序列" name="indicator_id">
                  <Select
                    options={(profile?.series ?? []).map((s) => ({
                      value: s.indicator_id,
                      label: `${s.name} (${s.frequency})`,
                    }))}
                  />
                </Form.Item>

                <Divider style={{ margin: "8px 0" }}>预测参数</Divider>

                <Form.Item label="预测方法" name="method">
                  <Select options={METHODS} />
                </Form.Item>
                <Form.Item label="预测期数 (月)" name="horizon_months">
                  <InputNumber min={1} max={60} step={1} style={{ width: "100%" }} />
                </Form.Item>
                <Form.Item name="include_attribution" valuePropName="checked">
                  <Checkbox>包含偏差归因</Checkbox>
                </Form.Item>
              </Form>
            )}
          </Card>
        </Col>

        {/* ── RIGHT: results ── */}
        <Col xs={24} md={16} lg={17} xl={18}>
          {error ? (
            <Alert
              type="error"
              message="预测失败"
              description={error}
              showIcon
              style={{ marginBottom: 16 }}
            />
          ) : null}

          {loading ? (
            <Card>
              <div style={{ textAlign: "center", padding: 40 }}>
                <Spin size="large" tip="预测中..." />
              </div>
            </Card>
          ) : result ? (
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              {/* ── Summary banner ── */}
              <Card size="small" styles={{ body: { padding: 12 } }}>
                <Space wrap>
                  <Tag color="blue">{result.line_id}</Tag>
                  <Text>
                    <b>{result.indicator_name}</b> ({result.frequency})
                  </Text>
                  <Text type="secondary">· 方法 =</Text>
                  <Tag>{result.method}</Tag>
                  <Text type="secondary">· 置信水平 =</Text>
                  <Text strong>{(result.confidence * 100).toFixed(0)}%</Text>
                </Space>
              </Card>

              {/* ── Forecast line + CI band chart ── */}
              <Card
                size="small"
                title="历史 + 未来预测 (带 95% 置信带)"
                styles={{ body: { padding: 8 } }}
              >
                <UniversalChart
                  type="line"
                  data={{
                    categories: [
                      ...result.historical.map((p) => p.period),
                      ...result.forecast.map((p) => p.period),
                    ],
                    values: [
                      ...result.historical.map((p) => p.point),
                      ...result.forecast.map((p) => p.point),
                    ],
                    series: ["forecast"],
                  }}
                  options={{
                    title: result.indicator_name,
                    xAxisLabel: "月份",
                    yAxisLabel: result.indicator_name,
                    height: 360,
                    echartsOverrides: {
                      tooltip: { trigger: "axis" },
                      legend: { top: 0, right: 0 },
                      // CI band via "lower" + "upper" lines + invisible
                      // bounding series. Simpler approach: use min/max
                      // shading via two extra series with areaStyle.
                      series: [
                        {
                          name: "CI 下界",
                          type: "line",
                          data: [
                            ...result.historical.map((p) => p.lower),
                            ...result.forecast.map((p) => p.lower),
                          ],
                          lineStyle: { opacity: 0 },
                          stack: "ci-lower",
                          symbol: "none",
                          tooltip: { show: false },
                        },
                        {
                          name: "置信区间",
                          type: "line",
                          data: [
                            ...result.historical.map(
                              (p) => Math.max(0, p.upper - p.lower),
                            ),
                            ...result.forecast.map(
                              (p) => Math.max(0, p.upper - p.lower),
                            ),
                          ],
                          lineStyle: { opacity: 0 },
                          areaStyle: { color: "rgba(22,119,255,0.18)" },
                          stack: "ci-band",
                          symbol: "none",
                        },
                        {
                          name: "实际值",
                          type: "line",
                          data: [
                            ...result.historical.map((p) => p.point),
                            ...result.forecast.map(() => null),
                          ],
                          itemStyle: { color: "#1677ff" },
                          lineStyle: { width: 2 },
                          symbol: "circle",
                          symbolSize: 6,
                          markArea: {
                            silent: true,
                            itemStyle: { color: "rgba(250,140,22,0.06)" },
                            data: [
                              [
                                { xAxis: result.historical.length - 1 },
                                { xAxis: result.historical.length + result.forecast.length - 1 },
                              ],
                            ],
                          },
                        },
                        {
                          name: "预测值",
                          type: "line",
                          data: [
                            ...result.historical.map(() => null),
                            ...result.forecast.map((p) => p.point),
                          ],
                          itemStyle: { color: "#fa8c16" },
                          lineStyle: { width: 2, type: "dashed" },
                          symbol: "circle",
                          symbolSize: 6,
                        },
                      ],
                    },
                  }}
                />
              </Card>

              {/* ── MAPE / Bias metrics ── */}
              <Card
                size="small"
                title="模型评估 (末 6 期)"
                styles={{ body: { padding: 12 } }}
              >
                <Row gutter={16}>
                  <Col xs={24} sm={8}>
                    <Statistic
                      title="MAPE (平均绝对百分比误差)"
                      value={
                        result.mape === null ? "—" : fmtPct(result.mape, 2)
                      }
                      valueStyle={{
                        color:
                          result.mape === null
                            ? undefined
                            : result.mape < 0.1
                              ? "#52c41a"
                              : result.mape < 0.2
                                ? "#fa8c16"
                                : "#cf1322",
                      }}
                    />
                  </Col>
                  <Col xs={24} sm={8}>
                    <Statistic
                      title="Bias (平均偏差)"
                      value={
                        result.bias === null ? "—" : fmtNum(result.bias, 4)
                      }
                      valueStyle={{
                        color:
                          result.bias === null
                            ? undefined
                            : result.bias > 0
                              ? "#52c41a"
                              : result.bias < 0
                                ? "#cf1322"
                                : undefined,
                      }}
                    />
                  </Col>
                  <Col xs={24} sm={8}>
                    <Statistic
                      title="置信水平"
                      value={fmtPct(result.confidence, 0)}
                    />
                  </Col>
                </Row>
              </Card>

              {/* ── Attribution table ── */}
              {result.attribution && result.attribution.length > 0 ? (
                <Card
                  size="small"
                  title="偏差归因 · 4 大类因素权重"
                  styles={{ body: { padding: 8 } }}
                >
                  <Table<AttributionRow>
                    dataSource={result.attribution}
                    rowKey="factor_id"
                    size="small"
                    pagination={false}
                    columns={[
                      {
                        title: "因素",
                        dataIndex: "factor_name",
                        key: "factor_name",
                        render: (v, r) => (
                          <Tag color="blue">{v || r.factor_id}</Tag>
                        ),
                      },
                      {
                        title: "权重",
                        dataIndex: "weight",
                        key: "weight",
                        render: (v) => (
                          <Text strong>{fmtPct(Number(v), 1)}</Text>
                        ),
                      },
                      {
                        title: "主要 driver",
                        dataIndex: "top_driver",
                        key: "top_driver",
                        render: (v) => <Text type="secondary">{v || "—"}</Text>,
                      },
                    ]}
                  />
                </Card>
              ) : null}
            </Space>
          ) : (
            <Card>
              <EmptyState
                title="选择参数后点击 Run Forecast"
                description="左面板选定业务线、KPI 和预测方法,点 Run Forecast 即可看到历史 + 预测曲线、95% 置信带、模型误差指标和偏差归因。"
              />
            </Card>
          )}
        </Col>
      </Row>
    </div>
  );
}
