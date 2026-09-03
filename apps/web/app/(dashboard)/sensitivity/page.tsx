// apps/web/app/(dashboard)/sensitivity/page.tsx
//
// Sensitivity Lab — universal across business lines.
//
// Layout:
//   ┌────────────┬────────────────────────────────────────────┐
//   │  Params    │  2D heatmap (top)                          │
//   │  panel     │                                            │
//   │  (left)    │  Tornado chart (mid)                       │
//   │            │                                            │
//   │  320px     │  Scenarios table (bottom)                  │
//   └────────────┴────────────────────────────────────────────┘
//
// Data flow:
//   1. On mount, GET /api/sensitivity/profiles → fills dropdowns.
//   2. User clicks [Run Analysis] → POST /api/sensitivity/analyze →
//      render heatmap + tornado + scenarios.
//   3. While loading, show a Spin. On error, an inline Alert.
//
// All data is fetched client-side because the components use
// @ant-design/icons and ECharts, which require the browser.

"use client";

import { useEffect, useMemo, useState } from "react";
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
  Table,
  Tag,
  Typography,
} from "antd";
import { ExperimentOutlined, ReloadOutlined } from "@ant-design/icons";
import { EmptyState, UniversalChart } from "@biz-bp/ui";

const { Title, Paragraph, Text } = Typography;

interface InputDef {
  id: string;
  name: string;
  unit: string;
  default_range: [number, number];
  default_step: number;
  description?: string;
}

interface OutputDef {
  id: string;
  name: string;
  unit: string;
  base_value_ref?: string;
}

interface Profile {
  line_id: string;
  line_name: string;
  input_count: number;
  output_count: number;
  inputs: InputDef[];
  outputs: OutputDef[];
}

interface ProfilesResponse {
  count: number;
  profiles: Profile[];
}

interface TornadoBar {
  input_id: string;
  input_name: string;
  value_at_min: number;
  value_at_max: number;
  span: number;
  direction: "up" | "down" | "flat";
}

interface Scenario {
  name: string;
  label: string;
  input1_delta: number;
  input2_delta: number;
  output_value: number;
  delta_from_base: number;
  delta_pct: number;
}

interface MatrixLabels {
  col_name: string;
  row_name: string;
  col_labels: string[];
  row_labels: string[];
  col_var: string;
  row_var: string;
}

interface AnalyzeResult {
  line_id: string;
  output_id: string;
  output_name: string;
  output_unit: string;
  base_value: number;
  base_inputs: Record<string, number>;
  matrix: number[][];
  matrix_labels: MatrixLabels;
  tornado: TornadoBar[];
  scenarios: Scenario[];
  input_definitions: InputDef[];
  output_definitions: (OutputDef & { coefficients?: Record<string, number> })[];
}

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8769";

function fmtPct(x: number, digits = 1): string {
  return `${(x * 100).toFixed(digits)}%`;
}

function fmtNum(x: number, digits = 2): string {
  if (!Number.isFinite(x)) return "—";
  return x.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export default function SensitivityPage() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loadingProfiles, setLoadingProfiles] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);

  const [form] = Form.useForm();
  const lineId = Form.useWatch("line_id", form);
  const input1Id = Form.useWatch("input1_id", form);
  const input2Id = Form.useWatch("input2_id", form);
  const outputId = Form.useWatch("output_id", form);
  const use2D = Form.useWatch("use_2d", form);

  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRun, setHasRun] = useState(false);

  // ── Load profiles on mount ──
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/sensitivity/profiles", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as ProfilesResponse;
        if (!cancelled) {
          setProfiles(data.profiles ?? []);
          setProfileError(null);
          // Default-select the first line + first output + first input pair.
          const first = data.profiles?.[0];
          if (first) {
            form.setFieldsValue({
              line_id: first.line_id,
              output_id: first.outputs[0]?.id,
              input1_id: first.inputs[0]?.id,
              input2_id: first.inputs[1]?.id ?? first.inputs[0]?.id,
              use_2d: true,
              input1_range_lo: first.inputs[0]?.default_range?.[0] ?? -0.1,
              input1_range_hi: first.inputs[0]?.default_range?.[1] ?? 0.1,
              input1_step: first.inputs[0]?.default_step ?? 0.02,
              input2_range_lo: first.inputs[1]?.default_range?.[0] ?? -0.1,
              input2_range_hi: first.inputs[1]?.default_range?.[1] ?? 0.1,
              input2_step: first.inputs[1]?.default_step ?? 0.05,
              base_override: 0,
              use_base_override: false,
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
  }, [form]);

  // ── Profile / input / output selectors ──
  const profile = useMemo(
    () => profiles.find((p) => p.line_id === lineId),
    [profiles, lineId],
  );

  // When the line changes, reset output/input selections.
  useEffect(() => {
    if (!profile) return;
    const currentOutput = form.getFieldValue("output_id");
    if (!profile.outputs.find((o) => o.id === currentOutput)) {
      form.setFieldsValue({
        output_id: profile.outputs[0]?.id,
        input1_id: profile.inputs[0]?.id,
        input2_id: profile.inputs[1]?.id ?? profile.inputs[0]?.id,
        input1_range_lo: profile.inputs[0]?.default_range?.[0] ?? -0.1,
        input1_range_hi: profile.inputs[0]?.default_range?.[1] ?? 0.1,
        input1_step: profile.inputs[0]?.default_step ?? 0.02,
        input2_range_lo: profile.inputs[1]?.default_range?.[0] ?? -0.1,
        input2_range_hi: profile.inputs[1]?.default_range?.[1] ?? 0.1,
        input2_step: profile.inputs[1]?.default_step ?? 0.05,
      });
    }
  }, [profile, form]);

  // Update range/step when input1 / input2 change.
  useEffect(() => {
    if (!profile || !input1Id) return;
    const i1 = profile.inputs.find((i) => i.id === input1Id);
    if (i1) {
      form.setFieldsValue({
        input1_range_lo: i1.default_range[0],
        input1_range_hi: i1.default_range[1],
        input1_step: i1.default_step,
      });
    }
  }, [profile, input1Id, form]);
  useEffect(() => {
    if (!profile || !input2Id || input2Id === input1Id) return;
    const i2 = profile.inputs.find((i) => i.id === input2Id);
    if (i2) {
      form.setFieldsValue({
        input2_range_lo: i2.default_range[0],
        input2_range_hi: i2.default_range[1],
        input2_step: i2.default_step,
      });
    }
  }, [profile, input2Id, input1Id, form]);

  // ── Run analysis ──
  async function runAnalyze() {
    if (!profile || !outputId || !input1Id) return;
    setError(null);
    setLoading(true);
    setHasRun(true);
    try {
      const values = form.getFieldsValue();
      const useBaseOverride = Boolean(values.use_base_override);
      const body: Record<string, unknown> = {
        line_id: profile.line_id,
        output_id: outputId,
        input1_id: input1Id,
        input2_id: use2D && input2Id && input2Id !== input1Id ? input2Id : null,
        input1_range: [values.input1_range_lo, values.input1_range_hi],
        input1_step: values.input1_step,
        base_overrides: useBaseOverride
          ? { [outputId]: Number(values.base_override) || 0 }
          : {},
      };
      if (use2D && input2Id && input2Id !== input1Id) {
        body.input2_range = [values.input2_range_lo, values.input2_range_hi];
        body.input2_step = values.input2_step;
      }
      const res = await fetch("/api/sensitivity/analyze", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail ?? `HTTP ${res.status}`);
      }
      const data = (await res.json()) as AnalyzeResult;
      setResult(data);
    } catch (e) {
      setError((e as Error).message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  // Auto-run on first profile load so the page isn't empty.
  useEffect(() => {
    if (profiles.length > 0 && !hasRun && !loading) {
      runAnalyze();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profiles.length]);

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          <ExperimentOutlined /> 敏感性分析实验室
        </Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          通用引擎 — 选择业务线、调整输入因子范围,观察输出指标如何变化。所有业务线共用同一套数学模型(线性系数)。
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
                onClick={runAnalyze}
                loading={loading}
                disabled={!profile || !outputId || !input1Id}
                size="small"
              >
                Run Analysis
              </Button>
            }
          >
            {loadingProfiles ? (
              <Skeleton active paragraph={{ rows: 6 }} />
            ) : profileError ? (
              <Alert type="error" message={`加载 profiles 失败: ${profileError}`} />
            ) : profiles.length === 0 ? (
              <EmptyState
                title="尚无业务线配置敏感性"
                description="在 business_lines/<line>/sensitivity.yaml 添加配置后即可使用。"
              />
            ) : (
              <Form form={form} layout="vertical" size="small">
                <Form.Item label="业务线" name="line_id">
                  <Select
                    options={profiles.map((p) => ({
                      value: p.line_id,
                      label: `${p.line_name} (${p.input_count} in / ${p.output_count} out)`,
                    }))}
                  />
                </Form.Item>
                <Form.Item label="输出指标 (Y)" name="output_id">
                  <Select
                    options={(profile?.outputs ?? []).map((o) => ({
                      value: o.id,
                      label: `${o.name} (${o.unit || "—"})`,
                    }))}
                  />
                </Form.Item>

                <Divider style={{ margin: "8px 0" }}>输入因子 (X)</Divider>

                <Form.Item label="Input 1 (X 轴)" name="input1_id">
                  <Select
                    options={(profile?.inputs ?? []).map((i) => ({
                      value: i.id,
                      label: `${i.name} (${i.unit || "—"})`,
                    }))}
                  />
                </Form.Item>
                <Row gutter={8}>
                  <Col span={12}>
                    <Form.Item label="X 范围 (min)" name="input1_range_lo">
                      <InputNumber step={0.02} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="X 范围 (max)" name="input1_range_hi">
                      <InputNumber step={0.02} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item label="X 步长" name="input1_step">
                  <InputNumber step={0.01} min={0.001} style={{ width: "100%" }} />
                </Form.Item>

                <Form.Item name="use_2d" valuePropName="checked">
                  <Checkbox>启用二维分析 (Y 轴)</Checkbox>
                </Form.Item>

                <Form.Item label="Input 2 (Y 轴)" name="input2_id">
                  <Select
                    disabled={!use2D}
                    options={(profile?.inputs ?? []).map((i) => ({
                      value: i.id,
                      label: `${i.name} (${i.unit || "—"})`,
                    }))}
                  />
                </Form.Item>
                <Row gutter={8}>
                  <Col span={12}>
                    <Form.Item label="Y 范围 (min)" name="input2_range_lo">
                      <InputNumber
                        step={0.02}
                        style={{ width: "100%" }}
                        disabled={!use2D}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="Y 范围 (max)" name="input2_range_hi">
                      <InputNumber
                        step={0.02}
                        style={{ width: "100%" }}
                        disabled={!use2D}
                      />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item label="Y 步长" name="input2_step">
                  <InputNumber
                    step={0.01}
                    min={0.001}
                    style={{ width: "100%" }}
                    disabled={!use2D}
                  />
                </Form.Item>

                <Divider style={{ margin: "8px 0" }}>基准值</Divider>
                <Form.Item name="use_base_override" valuePropName="checked">
                  <Checkbox>手动覆盖基准值</Checkbox>
                </Form.Item>
                <Form.Item shouldUpdate noStyle>
                  {() =>
                    form.getFieldValue("use_base_override") ? (
                      <Form.Item
                        label={`基准 ${outputId ?? ""}`}
                        name="base_override"
                        help="不勾选时,引擎会从业务线 API 自动拉取 (失败回退到 0)。"
                      >
                        <InputNumber step={0.01} style={{ width: "100%" }} />
                      </Form.Item>
                    ) : null
                  }
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
              message="分析失败"
              description={error}
              showIcon
              style={{ marginBottom: 16 }}
            />
          ) : null}

          {loading ? (
            <Card>
              <div style={{ textAlign: "center", padding: 40 }}>
                <Spin size="large" tip="分析中..." />
              </div>
            </Card>
          ) : result ? (
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              {/* ── Summary banner ── */}
              <Card size="small" styles={{ body: { padding: 12 } }}>
                <Space wrap>
                  <Tag color="blue">{result.line_id}</Tag>
                  <Text>
                    <b>{result.output_name}</b> ({result.output_unit || "—"})
                  </Text>
                  <Text type="secondary">· 基准值 =</Text>
                  <Text strong>{fmtNum(result.base_value, 4)}</Text>
                  <Text type="secondary">· X = {result.matrix_labels.col_name}</Text>
                  {result.matrix_labels.col_var !== result.matrix_labels.row_var && (
                    <Text type="secondary">· Y = {result.matrix_labels.row_name}</Text>
                  )}
                </Space>
              </Card>

              {/* ── 2D heatmap ── */}
              <Card
                size="small"
                title={`${result.output_name} · 2D 响应曲面`}
                styles={{ body: { padding: 8 } }}
              >
                {result.matrix.length === 1 ? (
                  <Text type="secondary">
                    一维分析: 共 {result.matrix[0].length} 个采样点。曲线已包含在 Tornado 排序中(扫掠输入已排除)。
                  </Text>
                ) : (
                  <UniversalChart
                    type="heatmap"
                    data={{
                      xCategories: result.matrix_labels.col_labels,
                      yCategories: result.matrix_labels.row_labels,
                      matrix: result.matrix,
                    }}
                    options={{
                      title: `${result.output_name} (${result.output_unit || ""})`,
                      xAxisLabel: `${result.matrix_labels.col_name} 扰动`,
                      yAxisLabel: `${result.matrix_labels.row_name} 扰动`,
                      height: 360,
                    }}
                  />
                )}
              </Card>

              {/* ── Tornado ── */}
              <Card
                size="small"
                title="Tornado · 哪个输入因子影响最大?"
                styles={{ body: { padding: 8 } }}
              >
                {result.tornado.length === 0 ? (
                  <EmptyState
                    title="无可比较的输入"
                    description="请选择至少 2 个输入因子,然后重新运行分析。"
                  />
                ) : (
                  <TornadoChart result={result} />
                )}
              </Card>

              {/* ── Scenarios ── */}
              <Card
                size="small"
                title="场景对比 · worst / base / best"
                styles={{ body: { padding: 8 } }}
              >
                <Table<Scenario>
                  dataSource={result.scenarios}
                  rowKey="name"
                  size="small"
                  pagination={false}
                  columns={[
                    {
                      title: "场景",
                      dataIndex: "label",
                      key: "label",
                      render: (v, r) => (
                        <Tag color={r.name === "base" ? "blue" : r.name === "best" ? "green" : "red"}>
                          {v}
                        </Tag>
                      ),
                    },
                    {
                      title: "Input 1 Δ",
                      dataIndex: "input1_delta",
                      key: "input1_delta",
                      render: (v) => fmtPct(Number(v), 1),
                    },
                    {
                      title: "Input 2 Δ",
                      dataIndex: "input2_delta",
                      key: "input2_delta",
                      render: (v) => fmtPct(Number(v), 1),
                    },
                    {
                      title: `${result.output_name}`,
                      dataIndex: "output_value",
                      key: "output_value",
                      render: (v) => (
                        <Text strong>{fmtNum(Number(v), 4)}</Text>
                      ),
                    },
                    {
                      title: "Δ from base",
                      dataIndex: "delta_from_base",
                      key: "delta",
                      render: (v) => {
                        const n = Number(v);
                        const color = n > 0 ? "#52c41a" : n < 0 ? "#cf1322" : "#888";
                        return (
                          <Text style={{ color }}>
                            {n > 0 ? "+" : ""}
                            {fmtNum(n, 4)}
                          </Text>
                        );
                      },
                    },
                    {
                      title: "Δ %",
                      dataIndex: "delta_pct",
                      key: "delta_pct",
                      render: (v) => {
                        const n = Number(v);
                        const color = n > 0 ? "#52c41a" : n < 0 ? "#cf1322" : "#888";
                        return (
                          <Text style={{ color }}>
                            {n > 0 ? "+" : ""}
                            {fmtNum(n, 2)}%
                          </Text>
                        );
                      },
                    },
                  ]}
                />
              </Card>
            </Space>
          ) : (
            <Card>
              <EmptyState
                title="选择参数后点击 Run Analysis"
                description="左面板选定业务线、输出、输入因子和范围,点 Run Analysis 即可看到响应曲面 / 龙卷风图 / 场景对比。"
              />
            </Card>
          )}
        </Col>
      </Row>
    </div>
  );
}

/**
 * Inline tornado chart. We render this directly with ECharts via
 * UniversalChart(type="bar") with the categories flipped: input names on
 * the Y axis (we rotate the bar series) and span on the X axis.
 */
function TornadoChart({ result }: { result: AnalyzeResult }) {
  // Largest span first; ECharts draws the first bar at the bottom, so we
  // reverse for top-to-bottom largest-first presentation.
  const items = [...result.tornado].sort((a, b) => a.span - b.span);
  const categories = items.map((t) => `${t.input_name} (${t.input_id})`);
  // For each bar, draw TWO segments: a transparent "min" placeholder so
  // the colored "span" segment starts at value_at_min and extends to
  // value_at_max. This produces the classic tornado visual.
  const placeholders = items.map((t) => Math.min(t.value_at_min, t.value_at_max));
  const spans = items.map((t) => Math.abs(t.value_at_max - t.value_at_min));
  const colors = items.map((t) =>
    t.direction === "up" ? "#52c41a" : t.direction === "down" ? "#cf1322" : "#888",
  );

  return (
    <UniversalChart
      type="bar"
      data={{
        categories,
        // We pre-bake a stacked series so the first (transparent) segment
        // anchors the visible bar at value_at_min.
        values: placeholders.concat(spans),
        series: ["placeholder", "span"],
      }}
      options={{
        title: `绝对影响 (${result.output_unit || ""})`,
        xAxisLabel: `${result.output_name} 变化`,
        yAxisLabel: "输入因子",
        height: Math.max(220, 40 + items.length * 28),
        echartsOverrides: {
          legend: { show: false },
          grid: { left: 160, right: 24, top: 16, bottom: 36, containLabel: true },
          xAxis: { type: "value" },
          yAxis: { type: "category", data: categories },
          series: [
            {
              name: "placeholder",
              type: "bar",
              stack: "tornado",
              itemStyle: { color: "transparent" },
              emphasis: { itemStyle: { color: "transparent" } },
              data: placeholders,
            },
            {
              name: "span",
              type: "bar",
              stack: "tornado",
              data: spans.map((v, i) => ({ value: v, itemStyle: { color: colors[i] } })),
              label: {
                show: true,
                position: "right",
                formatter: (p: { value: number }) => fmtNum(p.value, 4),
              },
            },
          ],
        },
      }}
    />
  );
}
