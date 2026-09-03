// apps/web/app/(dashboard)/alerts/page.tsx
//
// Alert Center — universal across business lines.
//
// Layout:
//   ┌─────────────────────────────────────────────────────────────┐
//   │ line selector + severity tabs + [立即检查] 按钮              │
//   ├─────────────────────────────────────────────────────────────┤
//   │ Triggered alerts (cards, severity color bar + actions)        │
//   ├─────────────────────────────────────────────────────────────┤
//   │ Rules (read-only, expandable to view YAML)                  │
//   └─────────────────────────────────────────────────────────────┘

"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Alert as AntAlert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Skeleton,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import {
  AlertOutlined,
  CheckOutlined,
  DeleteOutlined,
  ReloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { EmptyState } from "@biz-bp/ui";

const { Title, Paragraph, Text } = Typography;

type Severity = "critical" | "high" | "medium" | "low";

const SEVERITY_COLOR: Record<Severity, string> = {
  critical: "#cf1322",
  high: "#fa541c",
  medium: "#fa8c16",
  low: "#1677ff",
};

const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
};

interface RuleDef {
  id: string;
  name: string;
  indicator_id: string;
  operator: string;
  threshold: unknown;
  consecutive: number | null;
  severity: Severity;
  message_template: string;
  enabled: boolean;
  channels: string[];
  scope: string;
}

interface AttributionDef {
  id: string;
  name: string;
  drivers: string[];
}

interface RulesResponse {
  line_id: string;
  line_name: string;
  rule_count: number;
  rules: RuleDef[];
  attribution: AttributionDef[];
}

interface TriggeredAlert {
  alert_id: string;
  rule_id: string;
  rule_name: string;
  severity: Severity;
  message: string;
  line_id: string;
  target: string;
  target_name: string;
  metric_value: number | string;
  triggered_at: string;
  acknowledged: boolean;
}

interface HistoryResponse {
  line_id: string | null;
  total: number;
  limit: number;
  offset: number;
  items: TriggeredAlert[];
}

interface CheckResult {
  line_id: string;
  checked_at: string;
  rules_evaluated: number;
  alerts_triggered: TriggeredAlert[];
  summary: Record<Severity, number>;
}

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8769";

const SEVERITY_TABS = [
  { label: "全部", value: "all" },
  { label: "严重", value: "critical" },
  { label: "高", value: "high" },
  { label: "中", value: "medium" },
  { label: "低", value: "low" },
];

export default function AlertsPage() {
  const searchParams = useSearchParams();
  const initialLine = searchParams?.get("line") ?? null;

  const [lines, setLines] = useState<
    { id: string; name: string; rule_count: number }[]
  >([]);
  const [linesLoading, setLinesLoading] = useState(true);
  const [linesError, setLinesError] = useState<string | null>(null);

  const [lineId, setLineId] = useState<string | null>(initialLine);
  const [rules, setRules] = useState<RulesResponse | null>(null);
  const [rulesLoading, setRulesLoading] = useState(false);

  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const [severityFilter, setSeverityFilter] = useState<string>("all");

  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [checkSummary, setCheckSummary] = useState<Record<Severity, number> | null>(
    null,
  );

  // ── Load available lines on mount ──
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Fetch both /api/alerts/profiles (for per-line rule_count)
        // and /api/registry/lines (for display_name) in parallel.
        // /api/alerts/profiles only returns line_id + rule_count;
        // we need the registry to show "住宅分析" instead of
        // "residential" in the dropdown. Failures on the registry
        // call are non-fatal: we fall back to the line_id as the
        // display name.
        const [profilesRes, registryRes] = await Promise.all([
          fetch("/api/alerts/profiles", { cache: "no-store" }),
          fetch("/api/registry/lines", { cache: "no-store" }),
        ]);
        if (!profilesRes.ok) {
          throw new Error(`HTTP ${profilesRes.status} from /api/alerts/profiles`);
        }
        const data = (await profilesRes.json()) as {
          count: number;
          lines: { line_id: string; rule_count: number }[];
        };
        let displayLookup: Record<string, string> = {};
        if (registryRes.ok) {
          const reg = (await registryRes.json()) as {
            lines?: { id: string; name?: string; display_name?: string }[];
          };
          for (const r of reg.lines ?? []) {
            displayLookup[r.id] = r.display_name ?? r.name ?? r.id;
          }
        }
        if (!cancelled) {
          setLines(
            data.lines.map((l) => ({
              id: l.line_id,
              // Prefer the registry display_name; fall back to the
              // profile's line_id if the registry didn't have it
              // (older line or transient registry fetch failure).
              name: displayLookup[l.line_id] ?? l.line_id,
              rule_count: l.rule_count,
            })),
          );
          setLinesError(null);
          if (data.lines.length > 0 && !lineId) {
            // Honor ?line= query param if it matches a known line.
            const wanted = initialLine
              ? data.lines.find((l) => l.line_id === initialLine)
              : undefined;
            setLineId((wanted ?? data.lines[0]).line_id);
          }
        }
      } catch (e) {
        if (!cancelled) setLinesError((e as Error).message);
      } finally {
        if (!cancelled) setLinesLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Load rules + history when line changes ──
  useEffect(() => {
    if (!lineId) return;
    let cancelled = false;
    setRulesLoading(true);
    setRules(null);
    setHistory(null);
    (async () => {
      try {
        const r = await fetch(`/api/alerts/rules/${encodeURIComponent(lineId)}`, {
          cache: "no-store",
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const rdata = (await r.json()) as RulesResponse;
        if (!cancelled) setRules(rdata);
      } catch {
        if (!cancelled) setRules(null);
      } finally {
        if (!cancelled) setRulesLoading(false);
      }
    })();
    refreshHistory(lineId).catch(() => {
      if (!cancelled) setHistory(null);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineId]);

  // ── Poll history every 10s (in-app channel polling) ──
  useEffect(() => {
    if (!lineId) return;
    const id = setInterval(() => {
      refreshHistory(lineId).catch(() => undefined);
    }, 10_000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineId]);

  async function refreshHistory(lid: string) {
    setHistoryLoading(true);
    try {
      const res = await fetch(
        `/api/alerts/history?line_id=${encodeURIComponent(lid)}&limit=100`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as HistoryResponse;
      setHistory(data);
    } finally {
      setHistoryLoading(false);
    }
  }

  // ── Run check ──
  async function runCheck() {
    if (!lineId) return;
    setChecking(true);
    setCheckError(null);
    try {
      const res = await fetch("/api/alerts/check", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ line_id: lineId }),
        cache: "no-store",
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail ?? `HTTP ${res.status}`);
      }
      const data = (await res.json()) as CheckResult;
      setCheckSummary(data.summary);
      await refreshHistory(lineId);
    } catch (e) {
      setCheckError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }

  // ── Acknowledge / delete ──
  async function acknowledge(alertId: string) {
    await fetch(`/api/alerts/acknowledge/${encodeURIComponent(alertId)}`, {
      method: "POST",
      cache: "no-store",
    });
    if (lineId) await refreshHistory(lineId);
  }
  async function remove(alertId: string) {
    await fetch(`/api/alerts/${encodeURIComponent(alertId)}`, {
      method: "DELETE",
      cache: "no-store",
    });
    if (lineId) await refreshHistory(lineId);
  }

  const filtered = useMemo(() => {
    const items = history?.items ?? [];
    if (severityFilter === "all") return items;
    return items.filter((a) => a.severity === severityFilter);
  }, [history, severityFilter]);

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          <AlertOutlined /> 告警中心
        </Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          通用引擎 — 检查所有业务线的阈值/趋势/持续性告警,标记确认、忽略或深入分析。仅 in-app 通知渠道已实现;email / webhook 预留接口。
        </Paragraph>
      </Space>

      {/* ── Top bar: line selector + severity tabs + Check button ── */}
      <Card size="small" style={{ marginBottom: 16 }} styles={{ body: { padding: 12 } }}>
        <Row gutter={12} align="middle">
          <Col xs={24} sm={8} md={6}>
            <Space size={4}>
              <Text type="secondary">业务线:</Text>
              <Select
                size="small"
                style={{ minWidth: 200 }}
                value={lineId ?? undefined}
                onChange={(v) => setLineId(v)}
                loading={linesLoading}
                options={lines.map((l) => ({
                  value: l.id,
                  label: `${l.name} (${l.rule_count} rules)`,
                }))}
              />
            </Space>
          </Col>
          <Col xs={24} sm={10} md={12}>
            <Segmented
              value={severityFilter}
              onChange={(v) => setSeverityFilter(v as string)}
              options={SEVERITY_TABS.map((t) => ({ label: t.label, value: t.value }))}
            />
          </Col>
          <Col xs={24} sm={6} md={6} style={{ textAlign: "right" }}>
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              onClick={runCheck}
              loading={checking}
              disabled={!lineId}
            >
              立即检查
            </Button>
          </Col>
        </Row>
        {checkSummary ? (
          <Row gutter={12} style={{ marginTop: 12 }}>
            <Col xs={12} sm={6}>
              <Statistic
                title="严重"
                value={checkSummary.critical ?? 0}
                valueStyle={{ color: SEVERITY_COLOR.critical }}
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title="高"
                value={checkSummary.high ?? 0}
                valueStyle={{ color: SEVERITY_COLOR.high }}
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title="中"
                value={checkSummary.medium ?? 0}
                valueStyle={{ color: SEVERITY_COLOR.medium }}
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title="低"
                value={checkSummary.low ?? 0}
                valueStyle={{ color: SEVERITY_COLOR.low }}
              />
            </Col>
          </Row>
        ) : null}
        {checkError ? (
          <AntAlert
            type="error"
            message="检查失败"
            description={checkError}
            showIcon
            style={{ marginTop: 12 }}
          />
        ) : null}
      </Card>

      {/* ── Triggered alerts ── */}
      <Card
        size="small"
        title={
          <Space>
            <WarningOutlined />
            触发的告警
            {history ? <Tag>{history.total}</Tag> : null}
          </Space>
        }
        style={{ marginBottom: 16 }}
      >
        {linesError ? (
          <AntAlert
            type="error"
            message={`加载业务线失败: ${linesError}`}
            showIcon
          />
        ) : linesLoading ? (
          <Skeleton active paragraph={{ rows: 3 }} />
        ) : !lineId ? (
          <Empty description="请选择业务线" />
        ) : historyLoading && !history ? (
          <Skeleton active paragraph={{ rows: 3 }} />
        ) : filtered.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <span>
                {history && history.total > 0
                  ? `当前 severity 过滤下没有告警(总 ${history.total} 条)`
                  : "暂无告警 — 点 [立即检查] 跑一次规则评估"}
              </span>
            }
          />
        ) : (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            {filtered.map((a) => (
              <AlertCard
                key={a.alert_id}
                alert={a}
                onAcknowledge={() => acknowledge(a.alert_id)}
                onRemove={() => remove(a.alert_id)}
              />
            ))}
          </Space>
        )}
      </Card>

      {/* ── Rules list (read-only) ── */}
      <Card size="small" title="规则列表 (只读)">
        {rulesLoading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : !rules || rules.rules.length === 0 ? (
          <EmptyState
            title="该业务线未配置告警规则"
            description="在 business_lines/<line>/alerts.yaml 添加规则后即可使用。"
          />
        ) : (
          <Collapse
            size="small"
            items={rules.rules.map((r) => ({
              key: r.id,
              label: (
                <Space>
                  <span
                    style={{
                      display: "inline-block",
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: SEVERITY_COLOR[r.severity] ?? "#888",
                    }}
                  />
                  <Text strong>{r.name}</Text>
                  <Tag color={r.severity === "critical" ? "red" : r.severity === "high" ? "volcano" : r.severity === "medium" ? "orange" : "blue"}>
                    {SEVERITY_LABEL[r.severity]}
                  </Tag>
                  {!r.enabled ? <Tag>disabled</Tag> : null}
                  <Text type="secondary">· {r.indicator_id} {r.operator} {r.threshold as string}</Text>
                </Space>
              ),
              children: (
                <Space direction="vertical" size={4}>
                  <Text>
                    <b>指标:</b> {r.indicator_id} &nbsp; <b>算子:</b>{" "}
                    {r.operator} &nbsp; <b>阈值:</b>{" "}
                    {JSON.stringify(r.threshold)}
                    {r.consecutive ? (
                      <>
                        {" "}
                        &nbsp; <b>连续:</b> {r.consecutive} 期
                      </>
                    ) : null}
                  </Text>
                  <Text>
                    <b>范围:</b> {r.scope} &nbsp; <b>渠道:</b>{" "}
                    {(r.channels ?? []).join(", ")}
                  </Text>
                  <Text>
                    <b>消息模板:</b> <code>{r.message_template}</code>
                  </Text>
                </Space>
              ),
            }))}
          />
        )}
      </Card>
    </div>
  );
}

/**
 * Single alert card: severity color bar + content + actions.
 */
function AlertCard({
  alert,
  onAcknowledge,
  onRemove,
}: {
  alert: TriggeredAlert;
  onAcknowledge: () => void;
  onRemove: () => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        border: "1px solid #f0f0f0",
        borderLeft: `4px solid ${SEVERITY_COLOR[alert.severity] ?? "#888"}`,
        borderRadius: 4,
        background: alert.acknowledged ? "#fafafa" : "#fff",
        opacity: alert.acknowledged ? 0.7 : 1,
      }}
    >
      <div style={{ flex: 1, padding: "10px 12px" }}>
        <Space direction="vertical" size={4} style={{ width: "100%" }}>
          <Space wrap>
            <Tag color={alert.severity === "critical" ? "red" : alert.severity === "high" ? "volcano" : alert.severity === "medium" ? "orange" : "blue"}>
              {SEVERITY_LABEL[alert.severity]}
            </Tag>
            <Text strong>{alert.rule_name}</Text>
            <Text type="secondary">· {alert.target_name}</Text>
            {alert.acknowledged ? <Tag color="green">已确认</Tag> : null}
          </Space>
          <Text>{alert.message}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            触发时间: {alert.triggered_at} &nbsp;·&nbsp; 当前值:{" "}
            <Text code>{String(alert.metric_value)}</Text>
          </Text>
        </Space>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 12px",
          borderLeft: "1px solid #f0f0f0",
        }}
      >
        {!alert.acknowledged ? (
          <Button
            size="small"
            type="primary"
            ghost
            icon={<CheckOutlined />}
            onClick={onAcknowledge}
          >
            确认
          </Button>
        ) : null}
        <Popconfirm
          title="忽略该告警?"
          description="将从历史中移除(软删除)。"
          okText="忽略"
          cancelText="取消"
          onConfirm={onRemove}
        >
          <Button size="small" danger icon={<DeleteOutlined />}>
            忽略
          </Button>
        </Popconfirm>
      </div>
    </div>
  );
}
