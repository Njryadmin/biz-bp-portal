// apps/web/app/(dashboard)/dashboard/page.tsx
//
// Overview page: grid of cards, one per registered business line. Each card
// shows the line's icon, display name and indicator count, and links to the
// first nav entry of that line.
//
// 'use client' is required because @ant-design/icons uses React.createContext
// internally, which is only available on the client. The page is a client
// component; data is fetched client-side via the BFF proxy at /api/registry.

'use client';

import { Card, Col, Row, Typography, Statistic, Space, Spin } from "antd";
import * as Icons from "@ant-design/icons";
import Link from "next/link";
import { useEffect, useState } from "react";
import { EmptyState } from "@fin-bp/ui";
import type { BusinessLine } from "@fin-bp/types";

const { Title, Paragraph } = Typography;

interface RegistryResponse {
  version?: string;
  lines: BusinessLine[];
}

function resolveIcon(name: string | undefined): React.ReactNode {
  if (!name) return <Icons.AppstoreOutlined />;
  const iconMap = Icons as unknown as Record<string, React.ComponentType>;
  const candidates = [name, `${name}Outlined`];
  for (const key of candidates) {
    const Cmp = iconMap[key] as React.ComponentType | undefined;
    if (Cmp) return <Cmp />;
  }
  return <Icons.AppstoreOutlined />;
}

function displayName(line: BusinessLine): string {
  return line.display_name ?? line.name ?? line.id;
}

function firstNavPath(line: BusinessLine): string {
  return line.nav?.[0]?.path ?? "/dashboard";
}

export default function DashboardHome() {
  const [registry, setRegistry] = useState<RegistryResponse>({ lines: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Use BFF proxy to avoid CORS (apps/web/app/api/registry/route.ts)
        const res = await fetch("/api/registry", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as RegistryResponse;
        if (!cancelled) {
          setRegistry(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError((e as Error).message);
          setRegistry({ lines: [] });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const sorted = [...registry.lines].sort((a, b) =>
    displayName(a).localeCompare(displayName(b), "zh-Hans-CN", { sensitivity: "base" }),
  );

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          Overview
        </Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          Cross-business-line snapshot. Each card represents a business line
          registered in <code>business_lines/registry.yaml</code>. Registry version{" "}
          <code>v{registry.version ?? "?"}</code>.
        </Paragraph>
      </Space>

      {loading ? (
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin size="large" tip="Loading registry..." />
        </div>
      ) : error ? (
        <EmptyState
          title="Failed to load registry"
          description={`Error: ${error}. Make sure the API is running on port 8769.`}
        />
      ) : sorted.length === 0 ? (
        <EmptyState
          title="No business lines registered yet"
          description="Add your first business line to get started."
          docsHref="/docs/plugin-howto.md"
          docsLabel="Read the plugin how-to"
        />
      ) : (
        <Row gutter={[16, 16]}>
          {sorted.map((line) => (
            <Col key={line.id} xs={24} sm={12} md={8} lg={6} xl={6}>
              <Link
                href={firstNavPath(line)}
                style={{ display: "block", color: "inherit" }}
              >
                <Card
                  hoverable
                  size="small"
                  styles={{ body: { padding: 16 } }}
                >
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    <Space size={8} align="center">
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          width: 32,
                          height: 32,
                          borderRadius: 6,
                          background: "#e6f4ff",
                          color: "#1677ff",
                          fontSize: 18,
                        }}
                      >
                        {resolveIcon(line.icon)}
                      </span>
                      <Title level={5} style={{ margin: 0 }}>
                        {displayName(line)}
                      </Title>
                    </Space>
                    <Paragraph
                      type="secondary"
                      style={{ minHeight: 40, margin: 0, fontSize: 12 }}
                      ellipsis={{ rows: 2 }}
                    >
                      {line.description || "No description provided."}
                    </Paragraph>
                    <Space size={16} style={{ width: "100%", justifyContent: "space-between" }}>
                      <Statistic
                        title="Indicators"
                        value={line.indicators_count ?? 0}
                        valueStyle={{ fontSize: 16 }}
                      />
                      <Statistic
                        title="Version"
                        value={line.version}
                        valueStyle={{ fontSize: 14 }}
                      />
                    </Space>
                  </Space>
                </Card>
              </Link>
            </Col>
          ))}
        </Row>
      )}
    </div>
  );
}
