// apps/web/app/(dashboard)/dashboard/_components/PerspectiveDashboard.tsx
//
// E (2026-09-04): Shared renderer for the per-perspective dashboard
// pages. Used by /dashboard/fin, /dashboard/hr, and the
// (forthcoming) /dashboard/shared overview if needed. Keeps the three
// pages in lock-step — the only differences are the title, the BFF
// path, and the forbidden-state copy.

"use client";

import { useEffect, useState } from "react";
import { Card, Col, Row, Spin, Typography, notification } from "antd";
import { EmptyState } from "@biz-bp/ui";
import type { DashboardResponse, DashboardView } from "@biz-bp/types";
import { apiFetch } from "../../../../lib/api";

const { Title, Paragraph } = Typography;

export interface PerspectiveDashboardProps {
  view: DashboardView;
  title: string;
  description: string;
  forbiddenTitle: string;
  /** BFF path (e.g. "/api/dashboard/fin"). */
  apiPath: string;
  /** Subtitle shown next to each business line (e.g. "财务 KPI"). */
  lineSubtitle?: string;
}

interface LoadState {
  status: "loading" | "ok" | "error" | "forbidden";
  data?: DashboardResponse;
  message?: string;
}

function formatValue(v: number): string {
  if (!Number.isFinite(v)) return "—";
  if (v < 1000) return v.toFixed(2);
  if (v < 1_000_000) return v.toFixed(0);
  return v.toExponential(2);
}

function trendColor(trend: string | undefined): string {
  if (!trend || trend === "—") return "#8c8c8c";
  if (trend.startsWith("+")) return "#52c41a";
  if (trend.startsWith("-")) return "#ff4d4f";
  return "#8c8c8c";
}

export function PerspectiveDashboard({
  view,
  title,
  description,
  forbiddenTitle,
  apiPath,
  lineSubtitle,
}: PerspectiveDashboardProps) {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiFetch<DashboardResponse>(apiPath, { view });
        if (!cancelled) setState({ status: "ok", data });
      } catch (e) {
        if (cancelled) return;
        const err = e as Error & { status?: number };
        if (err.status === 403) {
          setState({
            status: "forbidden",
            message: err.message || "无访问权限",
          });
        } else if (err.status === 401) {
          notification.warning({
            message: "会话已过期",
            description: "请重新登录",
          });
          setState({ status: "error", message: "未登录" });
        } else {
          setState({ status: "error", message: err.message || "加载失败" });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiPath, view]);

  if (state.status === "loading") {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Spin size="large" tip={`加载 ${title} KPI 中…`} />
      </div>
    );
  }
  if (state.status === "forbidden") {
    return (
      <div style={{ padding: 24 }}>
        <EmptyState
          title={forbiddenTitle}
          description={
            state.message ??
            "请向管理员申请对应数据域的访问权限,或切换至其它视角。"
          }
        />
      </div>
    );
  }
  if (state.status === "error" || !state.data) {
    return (
      <div style={{ padding: 24 }}>
        <EmptyState
          title={`加载 ${title} 失败`}
          description={state.message ?? "请稍后重试,或检查 API 是否运行。"}
        />
      </div>
    );
  }

  const { lines, kpis } = state.data;
  const byLine = new Map<string, typeof kpis>();
  for (const l of lines) byLine.set(l.line_id, []);
  for (const k of kpis) {
    const arr = byLine.get(k.line_id);
    if (arr) arr.push(k);
  }

  return (
    <div style={{ padding: 24 }}>
      <Title level={3} style={{ margin: 0 }}>
        {title}
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        {description}
      </Paragraph>
      {lines.length === 0 ? (
        <EmptyState
          title="暂无业务线"
          description="您当前没有可访问的业务线。"
        />
      ) : (
        lines.map((line) => {
          const items = byLine.get(line.line_id) ?? [];
          return (
            <section
              key={line.line_id}
              aria-label={line.line_name}
              style={{ marginBottom: 24 }}
            >
              <Title level={4} style={{ margin: "0 0 8px" }}>
                {line.line_name}{" "}
                <span style={{ color: "#8c8c8c", fontWeight: 400, fontSize: 13 }}>
                  ({items.length} 个 KPI{lineSubtitle ? ` · ${lineSubtitle}` : ""})
                </span>
              </Title>
              {items.length === 0 ? (
                <Card size="small">
                  <Paragraph type="secondary" style={{ margin: 0 }}>
                    该业务线在此视角下暂无 KPI 卡片 (manifest 中对应 kpis 列表为空)。
                  </Paragraph>
                </Card>
              ) : (
                <Row gutter={[12, 12]}>
                  {items.map((kpi) => (
                    <Col key={kpi.kpi_id} xs={24} sm={12} md={8} lg={6}>
                      <Card size="small" hoverable>
                        <div
                          style={{
                            marginBottom: 4,
                            color: "#8c8c8c",
                            fontSize: 12,
                          }}
                        >
                          {kpi.title}
                        </div>
                        <div
                          style={{
                            display: "flex",
                            alignItems: "baseline",
                            gap: 6,
                          }}
                        >
                          <span
                            style={{
                              fontSize: 22,
                              fontWeight: 600,
                              color: "#1f1f1f",
                            }}
                          >
                            {formatValue(kpi.value)}
                          </span>
                          {kpi.unit ? (
                            <span style={{ color: "#8c8c8c", fontSize: 12 }}>
                              {kpi.unit}
                            </span>
                          ) : null}
                        </div>
                        {kpi.trend ? (
                          <div
                            style={{
                              marginTop: 4,
                              color: trendColor(kpi.trend),
                              fontSize: 12,
                            }}
                          >
                            {kpi.trend}
                          </div>
                        ) : null}
                      </Card>
                    </Col>
                  ))}
                </Row>
              )}
            </section>
          );
        })
      )}
    </div>
  );
}
