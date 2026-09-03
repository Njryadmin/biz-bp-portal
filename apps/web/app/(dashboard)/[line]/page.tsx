// apps/web/app/(dashboard)/[line]/page.tsx
//
// Dynamic business-line overview route. Renders a KPI grid + nav menu
// for whichever line matches the URL parameter. Pulls all metadata
// from the BFF proxy at /api/registry (NEVER imports business_lines/*
// directly — plugin-isolation rule).
//
// Falls back to a friendly "not integrated" message if the line id is
// unknown. The Next router still returns HTTP 200 — this is by design:
// the sidebar's group header links to this page for every registered
// line, and we don't want a 404 to interrupt the dashboard UX.

"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Card, Col, Row, Skeleton, Space, Spin, Typography } from "antd";
import { EmptyState, UniversalKpiCard } from "@biz-bp/ui";
import type { BusinessLine, Indicator } from "@biz-bp/types";
import {
  buildLinePageConfig,
  getPageSpec,
  isKnownLine,
  setLinePageConfig,
} from "../_components/linePageConfig";

const { Title, Paragraph } = Typography;

interface RegistryResponse {
  version?: string;
  lines: BusinessLine[];
}

interface IndicatorsResponse {
  line_id: string;
  indicators?: Indicator[];
  count?: number;
}

function displayName(line: BusinessLine): string {
  return line.display_name ?? line.name ?? line.id;
}

function navTitleFromSlug(line: BusinessLine, slug: string): string {
  const nav = line.nav ?? [];
  if (slug === "") {
    return line.nav?.[0]?.title ?? "概览";
  }
  const match = nav.find((n) => n.path === `/${line.id}/${slug}`);
  if (match) return match.title;
  return slug;
}

export default function LineOverviewPage() {
  const params = useParams<{ line: string }>();
  const lineId = (params?.line ?? "").toString();
  const known = isKnownLine(lineId);

  const [registry, setRegistry] = useState<RegistryResponse | null>(null);
  const [indicators, setIndicators] = useState<Indicator[]>([]);
  const [loadingRegistry, setLoadingRegistry] = useState(true);
  const [loadingIndicators, setLoadingIndicators] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 1. Fetch registry (line metadata)
  useEffect(() => {
    let cancelled = false;
    setLoadingRegistry(true);
    (async () => {
      try {
        const res = await fetch("/api/registry", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as RegistryResponse;
        if (cancelled) return;
        setRegistry(data);
        // P2 #3: build the dynamic page-spec table from the registry
        // so every (line, page) pair declared in `business_lines/<line>/manifest.yaml`
        // is wired into the App Router automatically. Idempotent — safe
        // to call from both the [line] and [line]/[page] pages.
        setLinePageConfig(buildLinePageConfig(data.lines ?? []));
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoadingRegistry(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 2. Fetch indicators for the line (if any)
  useEffect(() => {
    if (!known) {
      setLoadingIndicators(false);
      return;
    }
    let cancelled = false;
    setLoadingIndicators(true);
    setIndicators([]);
    const url = `/api/lines/${lineId}/indicators`;
    fetch(url, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: IndicatorsResponse | null) => {
        if (cancelled) return;
        setIndicators(data?.indicators ?? []);
      })
      .catch(() => {
        /* swallow — fall through to "no indicators" rendering */
      })
      .finally(() => {
        if (!cancelled) setLoadingIndicators(false);
      });
    return () => {
      cancelled = true;
    };
  }, [lineId, known]);

  const line = useMemo<BusinessLine | undefined>(
    () => registry?.lines.find((l) => l.id === lineId),
    [registry, lineId],
  );

  // ── Registry still loading: render a skeleton, NOT an error ──
  // `isKnownLine()` is only correct after the registry fetch has
  // populated the live spec table. Until then, every line looks
  // "unknown" — flashing the "未知业务线" empty state during the
  // pre-hydration paint looks like a broken page even though the
  // line is valid. Show a skeleton instead and only fall through
  // to the genuine-unknown empty state once loadingRegistry is false.
  if (loadingRegistry) {
    return (
      <div style={{ padding: 24 }}>
        <Skeleton active paragraph={{ rows: 4 }} />
      </div>
    );
  }

  // ── Unknown line id (after registry loaded) → friendly empty state, NOT a 404 ──
  if (!known) {
    return (
      <div style={{ padding: 24 }}>
        <EmptyState
          title="未知业务线"
          description={`未在 registry 中找到业务线: ${lineId || "(empty)"}。请检查 business_lines/registry.yaml。`}
          docsHref="/docs/plugin-howto.md"
          docsLabel="查看插件编写指南"
        />
      </div>
    );
  }

  if (error || !line) {
    return (
      <div style={{ padding: 24 }}>
        <EmptyState
          title="无法加载 registry"
          description={`Error: ${error ?? "line not found in registry response"}`}
        />
      </div>
    );
  }

  // ── my-line has no /indicators — render a "ping" status card instead ──
  const overviewSpec = getPageSpec(lineId, "");
  if (overviewSpec.kind === "ping-only") {
    return <MyLineOverview line={line} />;
  }

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          {displayName(line)} · 概览
        </Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          {line.description || "—"}
        </Paragraph>
      </Space>

      {loadingIndicators ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : indicators.length === 0 ? (
        <EmptyState
          title="该业务线尚未配置 KPI"
          description={`业务线 "${lineId}" 在 /api/lines/${lineId}/indicators 没有返回任何指标。`}
        />
      ) : (
        <Row gutter={[16, 16]}>
          {indicators.map((ind) => (
            <Col key={ind.id} xs={12} sm={8} md={6} lg={6} xl={4}>
              <UniversalKpiCard
                indicator={{
                  id: ind.id,
                  name: ind.title,
                  unit: ind.unit,
                  format: ind.format as
                    | "currency"
                    | "number"
                    | "percent"
                    | "ratio",
                }}
                value={null /* server returns no per-line value here */}
                loading={false}
                precision={2}
              />
            </Col>
          ))}
        </Row>
      )}

      <Title level={4} style={{ marginTop: 32 }}>
        导航
      </Title>
      <Row gutter={[12, 12]}>
        {(line.nav ?? []).map((n) => {
          const slug = n.path.startsWith(`/${lineId}/`)
            ? n.path.slice(lineId.length + 2)
            : "";
          return (
            <Col key={n.path} xs={24} sm={12} md={8} lg={6}>
              <Link href={n.path} style={{ color: "inherit" }}>
                <Card hoverable size="small" styles={{ body: { padding: 12 } }}>
                  <Space direction="vertical" size={2}>
                    <Typography.Text strong>{n.title}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {slug === ""
                        ? "概览"
                        : navTitleFromSlug(line, slug)}
                    </Typography.Text>
                  </Space>
                </Card>
              </Link>
            </Col>
          );
        })}
        {/* Cross-cutting Sensitivity Lab shortcut. Pre-selects the current
            line via ?line= query param (the page reads it on mount). */}
        <Col xs={24} sm={12} md={8} lg={6}>
          <Link
            href={`/sensitivity?line=${lineId}`}
            style={{ color: "inherit" }}
          >
            <Card
              hoverable
              size="small"
              styles={{ body: { padding: 12 } }}
              style={{ borderColor: "#1677ff" }}
            >
              <Space direction="vertical" size={2}>
                <Typography.Text strong>
                  <span style={{ color: "#1677ff" }}>·</span> 敏感性分析
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  跨业务线 Sensitivity Lab — 调整输入观察 KPI
                </Typography.Text>
              </Space>
            </Card>
          </Link>
        </Col>
        {/* AI Copilot shortcut — pre-selects the current line via ?line=
            query param so the Copilot focuses on this line's data. */}
        <Col xs={24} sm={12} md={8} lg={6}>
          <Link
            href={`/copilot?line=${lineId}`}
            style={{ color: "inherit" }}
          >
            <Card
              hoverable
              size="small"
              styles={{ body: { padding: 12 } }}
              style={{ borderColor: "#722ed1" }}
            >
              <Space direction="vertical" size={2}>
                <Typography.Text strong>
                  <span style={{ color: "#722ed1" }}>·</span> AI Copilot
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  用自然语言提问 — 答案附带可点击的引用卡片
                </Typography.Text>
              </Space>
            </Card>
          </Link>
        </Col>
        {/* Rolling Forecast shortcut — pre-selects the current line via
            ?line= query param so the Forecast page starts with this
            line's profile loaded. */}
        <Col xs={24} sm={12} md={8} lg={6}>
          <Link
            href={`/forecast?line=${lineId}`}
            style={{ color: "inherit" }}
          >
            <Card
              hoverable
              size="small"
              styles={{ body: { padding: 12 } }}
              style={{ borderColor: "#13c2c2" }}
            >
              <Space direction="vertical" size={2}>
                <Typography.Text strong>
                  <span style={{ color: "#13c2c2" }}>·</span> 滚动预测
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  12 个月 KPI 滚动预测 · 95% 置信带 · 偏差归因
                </Typography.Text>
              </Space>
            </Card>
          </Link>
        </Col>
        {/* Alert Center shortcut — opens the alerts page with this line
            pre-selected (read from ?line= on mount). */}
        <Col xs={24} sm={12} md={8} lg={6}>
          <Link
            href={`/alerts?line=${lineId}`}
            style={{ color: "inherit" }}
          >
            <Card
              hoverable
              size="small"
              styles={{ body: { padding: 12 } }}
              style={{ borderColor: "#fa541c" }}
            >
              <Space direction="vertical" size={2}>
                <Typography.Text strong>
                  <span style={{ color: "#fa541c" }}>·</span> 告警中心
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  阈值 / 趋势 / 持续性告警 — 跨业务线通用引擎
                </Typography.Text>
              </Space>
            </Card>
          </Link>
        </Col>
      </Row>
    </div>
  );
}

/**
 * my-line has no /indicators endpoint. Render a "ping status" panel that
 * confirms the line is wired up and lists its nav entries.
 */
function MyLineOverview({ line }: { line: BusinessLine }) {
  const [ping, setPing] = useState<{ ok: boolean; message?: string } | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/lines/my-line/ping`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled) return;
        if (data && data.status === "ok") {
          setPing({ ok: true, message: data.message });
        } else {
          setPing({ ok: false });
        }
      })
      .catch(() => !cancelled && setPing({ ok: false }));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          {displayName(line)} · 概览
        </Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          {line.description || "—"}
        </Paragraph>
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={4}>
          <Space>
            <span
              style={{
                display: "inline-block",
                width: 10,
                height: 10,
                borderRadius: "50%",
                background: ping?.ok ? "#52c41a" : "#cf1322",
              }}
            />
            <Typography.Text strong>
              {ping === null
                ? "检查中..."
                : ping.ok
                  ? "API 正常 (200)"
                  : "API 不可达"}
            </Typography.Text>
          </Space>
          {ping?.message ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {ping.message}
            </Typography.Text>
          ) : null}
        </Space>
      </Card>

      <Title level={4} style={{ marginTop: 16 }}>
        导航
      </Title>
      <Row gutter={[12, 12]}>
        {(line.nav ?? []).map((n) => (
          <Col key={n.path} xs={24} sm={12} md={8} lg={6}>
            <Link href={n.path} style={{ color: "inherit" }}>
              <Card hoverable size="small" styles={{ body: { padding: 12 } }}>
                <Space direction="vertical" size={2}>
                  <Typography.Text strong>{n.title}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {n.path}
                  </Typography.Text>
                </Space>
              </Card>
            </Link>
          </Col>
        ))}
      </Row>
    </div>
  );
}
