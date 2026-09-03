// apps/web/app/(dashboard)/copilot/page.tsx
//
// AI Copilot chat interface.
//
// Layout:
//   ┌──────────────────────────────────────────────────────────────┐
//   │  Header: title + backend badge + line selector              │
//   ├──────────────────────────────────────────────────────────────┤
//   │  Backend settings (collapsible) — current backend, key      │
//   │  status, model, call counts, "重新检测" / "清空"             │
//   ├──────────────────────────────────────────────────────────────┤
//   │  Message list (user / assistant bubbles)                     │
//   │   - user:   right-aligned, grey bg                           │
//   │   - AI:     left-aligned, white bg                           │
//   │             - markdown-ish answer text                       │
//   │             - fallback warning (if used_fallback=true)       │
//   │             - citation cards (clickable)                     │
//   │             - optional chart (bar)                           │
//   ├──────────────────────────────────────────────────────────────┤
//   │  Suggestion panel (collapsible, 6-8 starter questions)       │
//   │  Input box + send button + "用真实 LLM" toggle               │
//   └──────────────────────────────────────────────────────────────┘
//
// All data flows through the BFF proxies at /api/copilot/* so the
// browser never needs CORS config for the Python API.

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Empty,
  Input,
  Row,
  Select,
  Skeleton,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message as antdMessage,
} from "antd";
import {
  RobotOutlined,
  SendOutlined,
  UserOutlined,
  LinkOutlined,
  BulbOutlined,
  DatabaseOutlined,
  ReloadOutlined,
  SettingOutlined,
  KeyOutlined,
  CheckCircleTwoTone,
  CloseCircleTwoTone,
  WarningOutlined,
} from "@ant-design/icons";
import { UniversalChart } from "@fin-bp/ui";
import type { BusinessLine } from "@fin-bp/types";

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

// ─────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────

interface Citation {
  source: string;
  title: string;
  snippet: string;
  url: string | null;
}

interface ChartData {
  type?: "bar" | "line" | "pie" | string;
  title?: string;
  categories: string[];
  values: number[];
  yAxisLabel?: string;
}

interface CopilotResponse {
  question: string;
  answer: string;
  citations: Citation[];
  chart_data: ChartData | null;
  intent: string;
  confidence: number;
  backend: string;
  // New fields (additive — older payloads may not have them)
  used_fallback?: boolean;
  fallback_reason?: string | null;
  model?: string | null;
  debug?: Record<string, unknown> | null;
}

interface CopilotHealth {
  backend: string;
  available_lines: string[];
  api_base?: string;
  error?: string;
  // New fields
  configured_backend?: string;
  deepseek_key_present?: boolean;
  ollama_url?: string | null;
  model?: string | null;
  temperature?: number | null;
  used_fallback?: boolean;
  last_call_status?: string | null;
  last_error?: string | null;
  last_latency_ms?: number | null;
  call_count?: number;
  success_count?: number;
  primary_stats?: Record<string, unknown> | null;
}

interface CopilotSuggestions {
  by_line: Record<string, string[]>;
  common: string[];
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

let _msgSeq = 0;
const nextId = () => ++_msgSeq;

function backendColor(b: string): string {
  if (b === "deepseek") return "purple";
  if (b === "ollama") return "blue";
  if (b === "mock") return "default";
  return "default";
}

function intentLabel(i: string): string {
  // Friendly Chinese label for known intents.
  const map: Record<string, string> = {
    irr_top: "住宅 IRR Top",
    payment_low: "住宅回款",
    redlines: "三道红线",
    dedup_low: "去化速度",
    noi_top: "零售 NOI Top",
    renovation: "零售调改 NPV",
    collection: "零售收缴率",
    vacancy: "租赁空置期",
    benchmark: "租赁基准差",
    cross_overview: "跨业务线概览",
    line_indicators: "指标库",
    sensitivity: "敏感性分析",
    compare: "跨线对比",
    fallback_unknown: "未识别",
    error: "后端异常",
  };
  return map[i] ?? i;
}

function confidenceTone(c: number): "success" | "warning" | "default" {
  if (c >= 0.7) return "success";
  if (c >= 0.4) return "warning";
  return "default";
}

function lastCallStatusTone(
  s: string | null | undefined,
): "success" | "warning" | "error" | "default" {
  if (s === "ok") return "success";
  if (s === "timeout" || s === "error") return "error";
  return "default";
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export default function CopilotPage() {
  const searchParams = useSearchParams();
  const initialLine = searchParams?.get("line") ?? null;
  const [health, setHealth] = useState<CopilotHealth | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<CopilotSuggestions | null>(
    null,
  );
  const [lines, setLines] = useState<BusinessLine[]>([]);
  const [selectedLine, setSelectedLine] = useState<string | null>(initialLine);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [useRealLlm, setUseRealLlm] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Fetch health + suggestions + registry on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      await refreshHealth(cancelled, setHealth, setHealthLoading);
      try {
        const [s, r] = await Promise.all([
          fetch("/api/copilot/suggestions", { cache: "no-store" }).then((x) =>
            x.ok ? x.json() : null,
          ),
          fetch("/api/registry", { cache: "no-store" }).then((x) =>
            x.ok ? x.json() : null,
          ),
        ]);
        if (cancelled) return;
        if (s) setSuggestions(s);
        if (r?.lines) setLines(r.lines);
      } catch {
        // Fall through; the empty-state below will surface the error.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Auto-scroll to bottom on new message.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Flat suggestions list.
  const allSuggestions = useMemo(() => {
    if (!suggestions) return [] as { line: string | null; text: string }[];
    const out: { line: string | null; text: string }[] = [];
    for (const q of suggestions.common) {
      out.push({ line: null, text: q });
    }
    for (const [line, qs] of Object.entries(suggestions.by_line)) {
      for (const q of qs) {
        out.push({ line, text: q });
      }
    }
    return out;
  }, [suggestions]);

  // Whether a "real" LLM backend is configured (and thus available to toggle to).
  const realBackendAvailable = useMemo(() => {
    return (
      !!health?.deepseek_key_present ||
      (!!health?.ollama_url && health.ollama_url.length > 0)
    );
  }, [health]);

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || sending) return;
    const userMsg: Message = { id: nextId(), role: "user", text: trimmed };
    const pendingMsg: Message = {
      id: nextId(),
      role: "assistant",
      text: "",
      pending: true,
    };
    setMessages((m) => [...m, userMsg, pendingMsg]);
    setInput("");
    setSending(true);
    try {
      const res = await fetch("/api/copilot/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          question: trimmed,
          line_id: selectedLine ?? undefined,
          // If the user toggled "use real LLM", pass a hint to the API.
          // The backend honours this only when a real backend is configured.
          prefer_real_llm: useRealLlm || undefined,
        }),
      });
      const text = await res.text();
      const data: CopilotResponse | { detail: string } = text
        ? JSON.parse(text)
        : { detail: "(empty body)" };
      if (!res.ok) {
        const detail =
          "detail" in data ? data.detail : `HTTP ${res.status}`;
        setMessages((m) =>
          m.map((x) =>
            x.id === pendingMsg.id
              ? { ...x, pending: false, error: String(detail) }
              : x,
          ),
        );
        antdMessage.error(String(detail));
        return;
      }
      // After the ok-branch the data is a CopilotResponse.
      const okData = data as CopilotResponse;
      setMessages((m) =>
        m.map((x) =>
          x.id === pendingMsg.id
            ? { id: x.id, role: "assistant", text: okData.answer, raw: okData }
            : x,
        ),
      );
      // Refresh health (call counts / last status may have changed).
      void refreshHealth(false, setHealth, setHealthLoading);
    } catch (err) {
      setMessages((m) =>
        m.map((x) =>
          x.id === pendingMsg.id
            ? { ...x, pending: false, error: String(err) }
            : x,
        ),
      );
    } finally {
      setSending(false);
    }
  }

  function clear() {
    setMessages([]);
  }

  const backendName = health?.backend ?? "loading";
  const backendOk = !!health && !health.error;
  const backendIsReal =
    backendName === "deepseek" || backendName === "ollama";
  const backendIsFallback =
    health?.used_fallback === true && backendIsReal;

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", height: "calc(100vh - 56px)" }}>
      {/* ── Header ─────────────────────────────────────────────── */}
      <Row gutter={16} align="middle" style={{ marginBottom: 16 }}>
        <Col flex="auto">
          <Space align="center" size={12}>
            <RobotOutlined style={{ fontSize: 22, color: "#1677ff" }} />
            <Title level={3} style={{ margin: 0 }}>
              AI Copilot
            </Title>
            <Tooltip
              title={
                backendIsFallback
                  ? "DeepSeek/Ollama 失败,已自动降级到 mock"
                  : "后端健康状态"
              }
            >
              <Badge
                status={backendOk ? "success" : "error"}
                text={
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    后端:
                  </Text>
                }
              />
            </Tooltip>
            <Tag
              color={backendIsFallback ? "orange" : backendColor(backendName)}
              data-testid="backend-badge"
              style={{ cursor: "pointer" }}
              onClick={() => setShowSettings((v) => !v)}
            >
              {backendName === "loading"
                ? "..."
                : (backendName || "unknown").toUpperCase()}
              {backendIsFallback ? " · FALLBACK" : ""}
            </Tag>
            {health?.model && (
              <Tag color="geekblue" style={{ fontSize: 11 }}>
                {health.model}
              </Tag>
            )}
            {health?.available_lines && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {health.available_lines.length} 业务线
              </Text>
            )}
            <Button
              type="text"
              size="small"
              icon={<SettingOutlined />}
              onClick={() => setShowSettings((v) => !v)}
            >
              {showSettings ? "隐藏设置" : "后端设置"}
            </Button>
          </Space>
        </Col>
        <Col>
          <Space>
            <Select
              allowClear
              placeholder="限定业务线 (可选)"
              style={{ minWidth: 200 }}
              value={selectedLine ?? undefined}
              onChange={(v) => setSelectedLine(v ?? null)}
              options={lines.map((l) => ({
                value: l.id,
                label: l.display_name ?? l.name ?? l.id,
              }))}
            />
            <Button
              icon={<ReloadOutlined />}
              onClick={clear}
              disabled={messages.length === 0}
            >
              清空
            </Button>
          </Space>
        </Col>
      </Row>

      {health?.error && (
        <Alert
          type="error"
          showIcon
          message="无法连接 Copilot 后端"
          description={health.error}
          style={{ marginBottom: 12 }}
        />
      )}

      {/* ── Backend settings (collapsible) ─────────────────────── */}
      <BackendSettings
        visible={showSettings}
        health={health}
        loading={healthLoading}
        onRefresh={() => refreshHealth(false, setHealth, setHealthLoading, true)}
      />

      {/* ── Suggestions (collapsible) ──────────────────────────── */}
      <Collapse
        ghost
        defaultActiveKey={["suggestions"]}
        items={[
          {
            key: "suggestions",
            label: (
              <Space>
                <BulbOutlined />
                <Text strong>推荐问题</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  ({allSuggestions.length})
                </Text>
              </Space>
            ),
            children: (
              <div style={{ marginBottom: 12 }}>
                {!suggestions ? (
                  <Skeleton active paragraph={{ rows: 2 }} />
                ) : allSuggestions.length === 0 ? (
                  <Empty
                    description="暂无推荐问题"
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                  />
                ) : (
                  <Space wrap size={[8, 8]}>
                    {allSuggestions.map((s, i) => (
                      <Tag
                        key={i}
                        color={s.line ? "blue" : "default"}
                        style={{ cursor: "pointer", padding: "4px 10px" }}
                        onClick={() => ask(s.text)}
                        title={s.line ? `(${s.line}) ${s.text}` : s.text}
                      >
                        {s.text}
                      </Tag>
                    ))}
                  </Space>
                )}
              </div>
            ),
          },
        ]}
        style={{ marginBottom: 8 }}
      />

      {/* ── Message list ───────────────────────────────────────── */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: "auto",
          background: "#fafafa",
          border: "1px solid #f0f0f0",
          borderRadius: 8,
          padding: 16,
          marginBottom: 12,
        }}
      >
        {messages.length === 0 ? (
          <Empty
            description={
              <Space direction="vertical" size={4}>
                <Text type="secondary">
                  用自然语言提问 — 例如 "住宅 IRR 最高的 3 个项目"
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  每个回答都会附带可点击的引用卡片,可跳回真实数据。
                </Text>
              </Space>
            }
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            style={{ marginTop: 80 }}
          />
        ) : (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            {messages.map((m) => (
              <MessageBubble key={m.id} msg={m} showDebug={showDebug} />
            ))}
          </Space>
        )}
      </div>

      {/* ── Input ──────────────────────────────────────────────── */}
      <Space.Compact style={{ width: "100%" }}>
        <TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入问题后按 Enter 发送,Shift+Enter 换行"
          autoSize={{ minRows: 2, maxRows: 6 }}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              ask(input);
            }
          }}
          disabled={sending}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={() => ask(input)}
          loading={sending}
          style={{ height: "auto" }}
        >
          发送
        </Button>
      </Space.Compact>

      <div
        style={{
          marginTop: 8,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Space size={12} wrap>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {selectedLine
              ? `已限定业务线: ${selectedLine}`
              : "未限定业务线 — Copilot 会从所有已注册业务线中检索"}
          </Text>
          {realBackendAvailable && (
            <Tooltip
              title={
                backendIsReal
                  ? "已使用真实 LLM;关闭则降级到 mock"
                  : "切换后,下次提问将尝试调用真实 LLM(若失败会自动降级到 mock)"
              }
            >
              <Space size={6}>
                <Switch
                  size="small"
                  checked={useRealLlm}
                  onChange={setUseRealLlm}
                />
                <Text style={{ fontSize: 12 }}>
                  用真实 LLM {useRealLlm ? "ON" : "OFF"}
                </Text>
              </Space>
            </Tooltip>
          )}
        </Space>
        <Button
          type="link"
          size="small"
          onClick={() => setShowDebug((v) => !v)}
          style={{ padding: 0 }}
        >
          {showDebug ? "隐藏调试" : "显示调试"}
        </Button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Refresh helper
// ─────────────────────────────────────────────────────────────────────────

async function refreshHealth(
  cancelled: boolean,
  setHealth: (h: CopilotHealth | null) => void,
  setLoading: (l: boolean) => void,
  showMessage = false,
) {
  setLoading(true);
  try {
    const r = await fetch("/api/copilot/health", { cache: "no-store" });
    const data: CopilotHealth = r.ok ? await r.json() : { backend: "unknown", available_lines: [], error: `HTTP ${r.status}` };
    if (!cancelled) setHealth(data);
    if (showMessage && !r.ok) {
      antdMessage.error(`健康检查失败: ${data.error ?? r.status}`);
    } else if (showMessage) {
      antdMessage.success("健康检查已更新");
    }
  } catch (err) {
    if (!cancelled) {
      setHealth({ backend: "unknown", available_lines: [], error: String(err) });
    }
  } finally {
    setLoading(false);
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Backend settings panel
// ─────────────────────────────────────────────────────────────────────────

function BackendSettings({
  visible,
  health,
  loading,
  onRefresh,
}: {
  visible: boolean;
  health: CopilotHealth | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  if (!visible) return null;
  if (!health) {
    return (
      <Card size="small" style={{ marginBottom: 8 }} loading={loading}>
        <Skeleton active paragraph={{ rows: 3 }} />
      </Card>
    );
  }
  const isReal = health.backend === "deepseek" || health.backend === "ollama";
  const isFallback = health.used_fallback === true && isReal;
  const successRate =
    (health.call_count ?? 0) > 0
      ? Math.round(
          ((health.success_count ?? 0) / (health.call_count ?? 1)) * 100,
        )
      : null;
  return (
    <Card
      size="small"
      title={
        <Space>
          <SettingOutlined />
          <Text strong>后端设置</Text>
          <Tag color={backendColor(health.backend || "mock")}>
            当前后端:{(health.backend || "unknown").toUpperCase()}
          </Tag>
          {isFallback && (
            <Tag color="orange" icon={<WarningOutlined />}>
              降级中
            </Tag>
          )}
        </Space>
      }
      extra={
        <Button
          icon={<ReloadOutlined />}
          size="small"
          onClick={onRefresh}
          loading={loading}
        >
          重新检测
        </Button>
      }
      style={{ marginBottom: 8 }}
    >
      <Row gutter={16}>
        <Col xs={24} md={12}>
          <Descriptions
            size="small"
            column={1}
            colon={false}
            labelStyle={{ color: "#999", width: 110 }}
          >
            <Descriptions.Item label="配置后端">
              <Tag color={backendColor(health.configured_backend || "mock")}>
                {(health.configured_backend || "mock").toUpperCase()}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item
              label={
                <span>
                  <KeyOutlined /> DeepSeek Key
                </span>
              }
            >
              {health.deepseek_key_present ? (
                <Space>
                  <CheckCircleTwoTone twoToneColor="#52c41a" />
                  <Text type="success">已配置</Text>
                </Space>
              ) : (
                <Space>
                  <CloseCircleTwoTone twoToneColor="#ff4d4f" />
                  <Text type="secondary">未配置</Text>
                </Space>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="Ollama URL">
              {health.ollama_url || (
                <Text type="secondary">未设置</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="模型">
              {health.model ? (
                <Tag color="geekblue">{health.model}</Tag>
              ) : (
                <Text type="secondary">—</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="Temperature">
              {health.temperature != null
                ? health.temperature
                : "—"}
            </Descriptions.Item>
            <Descriptions.Item label="API Base">
              <Text code style={{ fontSize: 11 }}>
                {health.api_base || "—"}
              </Text>
            </Descriptions.Item>
          </Descriptions>
        </Col>
        <Col xs={24} md={12}>
          <Descriptions
            size="small"
            column={1}
            colon={false}
            labelStyle={{ color: "#999", width: 110 }}
          >
            <Descriptions.Item label="本次进程调用">
              {health.call_count ?? 0} 次(成功 {health.success_count ?? 0})
            </Descriptions.Item>
            <Descriptions.Item label="成功率">
              {successRate != null ? (
                <Tag color={successRate >= 80 ? "green" : successRate >= 50 ? "orange" : "red"}>
                  {successRate}%
                </Tag>
              ) : (
                <Text type="secondary">—</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="最近一次状态">
              <Tag
                color={
                  health.last_call_status === "ok"
                    ? "green"
                    : health.last_call_status === "timeout" ||
                        health.last_call_status === "error"
                      ? "red"
                      : "default"
                }
              >
                {health.last_call_status ?? "—"}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="最近延迟">
              {health.last_latency_ms != null
                ? `${health.last_latency_ms} ms`
                : "—"}
            </Descriptions.Item>
            <Descriptions.Item label="最近错误">
              {health.last_error ? (
                <Text type="danger" style={{ fontSize: 11 }}>
                  {health.last_error}
                </Text>
              ) : (
                <Text type="secondary">—</Text>
              )}
            </Descriptions.Item>
          </Descriptions>
        </Col>
      </Row>
      {(!health.deepseek_key_present && !health.ollama_url) && (
        <Alert
          style={{ marginTop: 12 }}
          type="info"
          showIcon
          message="未配置真实 LLM — 当前为 mock 后端(规则引擎,无网络调用)"
          description={
            <div>
              <div>
                要启用真实 LLM,在 <Text code>.env</Text> 里设置:
              </div>
              <ul style={{ marginTop: 6, marginBottom: 4, paddingLeft: 20 }}>
                <li>
                  DeepSeek (推荐):
                  <Text code> DEEPSEEK_API_KEY=sk-xxxxxxxx</Text> 然后重启 API
                </li>
                <li>
                  Ollama (本地):
                  <Text code> OLLAMA_BASE_URL=http://localhost:11434</Text>
                </li>
              </ul>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  配置完成后,点"重新检测"刷新状态;真实 LLM 失败时,系统会自动降级到 mock,不会让用户看到 500。
                </Text>
              </div>
            </div>
          }
        />
      )}
      {health.deepseek_key_present && (
        <Alert
          style={{ marginTop: 12 }}
          type="success"
          showIcon
          message={
            <Space>
              <KeyOutlined />
              DeepSeek API Key 已配置 — 当前后端将优先调用 DeepSeek
            </Space>
          }
          description={
            <Text type="secondary" style={{ fontSize: 12 }}>
              若 DeepSeek 调用失败,系统会自动降级到 mock 后端,并在响应里标记
              <Text code> used_fallback: true</Text>。失败原因可在下方"最近错误"查看。
            </Text>
          }
        />
      )}
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Message bubble
// ─────────────────────────────────────────────────────────────────────────

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  raw?: CopilotResponse;
  pending?: boolean;
  error?: string;
}

function MessageBubble({ msg, showDebug }: { msg: Message; showDebug: boolean }) {
  if (msg.role === "user") {
    return (
      <Row justify="end">
        <Col flex="auto" />
        <Col>
          <Card
            size="small"
            style={{
              background: "#f0f5ff",
              borderColor: "#adc6ff",
              maxWidth: 600,
            }}
            styles={{ body: { padding: "8px 12px" } }}
          >
            <Space size={6}>
              <UserOutlined style={{ color: "#1677ff" }} />
              <Text>{msg.text}</Text>
            </Space>
          </Card>
        </Col>
      </Row>
    );
  }

  // Assistant
  if (msg.pending) {
    return (
      <Row>
        <Col>
          <Card
            size="small"
            style={{ background: "#fff", minWidth: 80 }}
            styles={{ body: { padding: "8px 12px" } }}
          >
            <Space size={6}>
              <RobotOutlined style={{ color: "#1677ff" }} />
              <Spin size="small" />
              <Text type="secondary">思考中…</Text>
            </Space>
          </Card>
        </Col>
      </Row>
    );
  }

  if (msg.error) {
    return (
      <Row>
        <Col>
          <Alert
            type="error"
            showIcon
            message="请求失败"
            description={msg.error}
          />
        </Col>
      </Row>
    );
  }

  const raw = msg.raw;
  return (
    <Row>
      <Col style={{ maxWidth: "85%" }}>
        <Card
          size="small"
          style={{ background: "#fff" }}
          styles={{ body: { padding: "12px 14px" } }}
        >
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Space size={6} wrap>
              <RobotOutlined style={{ color: "#1677ff" }} />
              <Tag color="blue">{intentLabel(raw?.intent ?? "unknown")}</Tag>
              <Tag color={confidenceTone(raw?.confidence ?? 0)}>
                置信度 {((raw?.confidence ?? 0) * 100).toFixed(0)}%
              </Tag>
              <Tag color={backendColor(raw?.backend ?? "mock")}>
                {(raw?.backend ?? "mock").toUpperCase()}
              </Tag>
              {raw?.model && (
                <Tag color="geekblue" style={{ fontSize: 11 }}>
                  {raw.model}
                </Tag>
              )}
              {raw?.used_fallback && (
                <Tooltip
                  title={
                    raw.fallback_reason
                      ? `降级原因:${raw.fallback_reason}`
                      : "真实 LLM 失败,已降级到 mock"
                  }
                >
                  <Tag color="orange" icon={<WarningOutlined />}>
                    FALLBACK
                  </Tag>
                </Tooltip>
              )}
            </Space>
            {raw?.used_fallback && (
              <Alert
                type="warning"
                showIcon
                banner
                message={
                  raw.fallback_reason
                    ? `真实 LLM 调用失败,已自动降级到 mock 后端。错误:${raw.fallback_reason}`
                    : "真实 LLM 调用失败,已自动降级到 mock 后端。"
                }
                style={{ padding: "4px 10px" }}
              />
            )}
            <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
              {msg.text}
            </div>

            {/* Citations */}
            {raw?.citations && raw.citations.length > 0 && (
              <div>
                <Text strong style={{ fontSize: 12 }}>
                  <DatabaseOutlined /> 引用 ({raw.citations.length})
                </Text>
                <Row gutter={[8, 8]} style={{ marginTop: 4 }}>
                  {raw.citations.map((c, i) => (
                    <Col key={i} xs={24} sm={12} md={8}>
                      <Card
                        size="small"
                        hoverable={!!c.url}
                        onClick={() => c.url && (window.location.href = c.url)}
                        styles={{ body: { padding: 10 } }}
                        style={{ background: "#fafafa" }}
                      >
                        <Space direction="vertical" size={2} style={{ width: "100%" }}>
                          <Text strong style={{ fontSize: 13 }} ellipsis>
                            {c.title}
                          </Text>
                          <Text
                            type="secondary"
                            style={{ fontSize: 11 }}
                            ellipsis={{ tooltip: c.snippet }}
                          >
                            {c.snippet}
                          </Text>
                          <Text
                            type="secondary"
                            style={{ fontSize: 10, fontFamily: "monospace" }}
                            ellipsis
                          >
                            {c.source}
                          </Text>
                          {c.url ? (
                            <a
                              href={c.url}
                              style={{ fontSize: 11 }}
                              onClick={(e) => e.stopPropagation()}
                            >
                              <LinkOutlined /> 查看数据
                            </a>
                          ) : null}
                        </Space>
                      </Card>
                    </Col>
                  ))}
                </Row>
              </div>
            )}

            {/* Chart */}
            {raw?.chart_data && raw.chart_data.categories && (
              <div>
                <Text strong style={{ fontSize: 12 }}>
                  附图
                </Text>
                <UniversalChart
                  type={
                    (raw.chart_data.type as "line" | "bar" | "scatter" | "waterfall" | "heatmap" | undefined) ?? "bar"
                  }
                  data={{
                    categories: raw.chart_data.categories,
                    values: raw.chart_data.values,
                  }}
                  options={{
                    title: raw.chart_data.title,
                    height: 240,
                    yAxisLabel: raw.chart_data.yAxisLabel,
                  }}
                />
              </div>
            )}

            {/* Debug panel */}
            {showDebug && raw?.debug ? (
              <pre
                style={{
                  background: "#0f0f0f",
                  color: "#cfd9ff",
                  padding: 8,
                  borderRadius: 4,
                  fontSize: 11,
                  maxHeight: 200,
                  overflow: "auto",
                  margin: 0,
                }}
              >
                {JSON.stringify(raw.debug, null, 2)}
              </pre>
            ) : null}
          </Space>
        </Card>
      </Col>
    </Row>
  );
}
