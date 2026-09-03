// apps/web/app/(dashboard)/scrapers/page.tsx
//
// Market-data scraper cockpit.
//
// Layout:
//   ┌─────────────────────────────────────────────────────────────┐
//   │ Title + 立即全部运行 button + 上次抓取时间                     │
//   ├─────────────────────────────────────────────────────────────┤
//   │ Scraper cards (one per registered source)                     │
//   │   - name, schedule, status, last run, [立即运行]               │
//   │   - expand to show last 7 days of runs                        │
//   ├─────────────────────────────────────────────────────────────┤
//   │ Last 7 days timeline of all runs                              │
//   └─────────────────────────────────────────────────────────────┘

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert as AntAlert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Row,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tag,
  Timeline,
  Typography,
} from "antd";
import {
  CloudDownloadOutlined,
  GlobalOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  ShopOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { EmptyState } from "@fin-bp/ui";

const { Title, Paragraph, Text } = Typography;

interface ScraperSummary {
  source_id: string;
  name: string;
  schedule: string;
  enabled: boolean;
  last_run: null | {
    upload_id: string;
    filename: string;
    row_count: number;
    uploaded_at: string;
  };
  last_status: null | string;
}

interface ScraperHistoryItem {
  upload_id: string;
  filename: string;
  row_count: number;
  uploaded_at: string;
}

interface ScraperDetail extends ScraperSummary {
  history: ScraperHistoryItem[];
}

interface RunResult {
  source_id: string;
  name: string;
  status: string;
  rows: number;
  used_fallback: boolean;
  error: null | string;
  upload_id: null | string;
  fetched_at: string;
  elapsed_ms: number;
}

const SOURCE_ICON: Record<string, React.ReactNode> = {
  nbs_house_price: <GlobalOutlined />,
  lianjia_deals: <ShopOutlined />,
  policy_crawler: <ThunderboltOutlined />,
};

const STATUS_COLOR: Record<string, string> = {
  ok: "green",
  degraded: "orange",
  error: "red",
  disabled: "default",
};

export default function ScrapersPage() {
  const [summaries, setSummaries] = useState<ScraperSummary[]>([]);
  const [summariesLoading, setSummariesLoading] = useState(true);
  const [summariesError, setSummariesError] = useState<string | null>(null);

  const [details, setDetails] = useState<Record<string, ScraperDetail | null>>(
    {},
  );

  const [runningOne, setRunningOne] = useState<string | null>(null);
  const [runningAll, setRunningAll] = useState(false);
  const [lastRunResults, setLastRunResults] = useState<RunResult[] | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const loadSummaries = useCallback(async () => {
    setSummariesLoading(true);
    try {
      const res = await fetch("/api/scrapers", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as ScraperSummary[];
      setSummaries(data ?? []);
      setSummariesError(null);
    } catch (e) {
      setSummariesError((e as Error).message);
    } finally {
      setSummariesLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (sourceId: string) => {
    try {
      const res = await fetch(
        `/api/scrapers/detail/${encodeURIComponent(sourceId)}`,
        { cache: "no-store" },
      );
      if (!res.ok) return;
      const data = (await res.json()) as ScraperDetail;
      setDetails((d) => ({ ...d, [sourceId]: data }));
    } catch {
      setDetails((d) => ({ ...d, [sourceId]: null }));
    }
  }, []);

  useEffect(() => {
    loadSummaries();
  }, [loadSummaries]);

  useEffect(() => {
    summaries.forEach((s) => {
      if (!details[s.source_id]) void loadDetail(s.source_id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summaries]);

  async function runOne(sourceId: string) {
    setRunningOne(sourceId);
    setRunError(null);
    try {
      const res = await fetch(
        `/api/scrapers/run/${encodeURIComponent(sourceId)}`,
        { method: "POST", cache: "no-store" },
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail ?? `HTTP ${res.status}`);
      }
      const data = (await res.json()) as RunResult;
      setLastRunResults([data]);
      await loadSummaries();
      await loadDetail(sourceId);
    } catch (e) {
      setRunError((e as Error).message);
    } finally {
      setRunningOne(null);
    }
  }

  async function runAll() {
    setRunningAll(true);
    setRunError(null);
    try {
      const res = await fetch("/api/scrapers/run-all", {
        method: "POST",
        cache: "no-store",
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail ?? `HTTP ${res.status}`);
      }
      const data = (await res.json()) as { results: RunResult[] };
      setLastRunResults(data.results ?? []);
      await loadSummaries();
      for (const r of data.results ?? []) {
        await loadDetail(r.source_id);
      }
    } catch (e) {
      setRunError((e as Error).message);
    } finally {
      setRunningAll(false);
    }
  }

  const totalRows = useMemo(
    () => summaries.reduce((acc, s) => acc + (s.last_run?.row_count ?? 0), 0),
    [summaries],
  );

  // Build a unified timeline of the last 7 days of runs across all sources.
  const timeline = useMemo(() => {
    const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
    const items: Array<{ ts: number; source: string; rows: number; status: string }> = [];
    for (const sid of Object.keys(details)) {
      const d = details[sid];
      if (!d) continue;
      for (const h of d.history ?? []) {
        const t = Date.parse(h.uploaded_at);
        if (Number.isNaN(t) || t < cutoff) continue;
        items.push({
          ts: t,
          source: sid,
          rows: h.row_count,
          status: h.row_count > 0 ? "ok" : "empty",
        });
      }
    }
    return items.sort((a, b) => b.ts - a.ts);
  }, [details]);

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          <CloudDownloadOutlined /> 市场数据
        </Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          Web 抓取(Scraper)框架 — 国家统计局 70 城房价、链家公开成交、住建部/各地政策。
          抓取失败自动降级到 mock 数据,确保 DBT marts 永远有数据。Airflow 每周日 00:00 UTC 自动跑一次。
        </Paragraph>
      </Space>

      {/* ── Top bar: stats + run-all button ── */}
      <Card size="small" style={{ marginBottom: 16 }} styles={{ body: { padding: 12 } }}>
        <Row gutter={12} align="middle">
          <Col xs={12} sm={6}>
            <Statistic title="已注册 Scraper" value={summaries.length} />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic title="启用中" value={summaries.filter((s) => s.enabled).length} />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic title="最近总抓取行数" value={totalRows} />
          </Col>
          <Col xs={12} sm={6} style={{ textAlign: "right" }}>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={runningAll}
              onClick={runAll}
            >
              立即全部运行
            </Button>
            <Button
              style={{ marginLeft: 8 }}
              icon={<ReloadOutlined />}
              onClick={loadSummaries}
            >
              刷新
            </Button>
          </Col>
        </Row>
        {runError ? (
          <AntAlert
            type="error"
            showIcon
            message="抓取失败"
            description={runError}
            style={{ marginTop: 12 }}
          />
        ) : null}
      </Card>

      {/* ── Scraper cards ── */}
      {summariesError ? (
        <AntAlert
          type="error"
          showIcon
          message={`无法加载 scraper 列表: ${summariesError}`}
          style={{ marginBottom: 16 }}
        />
      ) : null}

      {summariesLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : summaries.length === 0 ? (
        <EmptyState
          title="还没有注册任何 scraper"
          description="在 apps/api/app/services/scrapers/scrapers/ 下加一个 .py 文件 + register() 即可。"
        />
      ) : (
        <Row gutter={[16, 16]}>
          {summaries.map((s) => {
            const detail = details[s.source_id] ?? null;
            return (
              <Col xs={24} md={12} lg={8} key={s.source_id}>
                <Card
                  size="small"
                  title={
                    <Space>
                      {SOURCE_ICON[s.source_id] ?? <GlobalOutlined />}
                      <Text strong>{s.name}</Text>
                    </Space>
                  }
                  extra={
                    <Tag color={s.enabled ? "blue" : "default"}>
                      {s.enabled ? "enabled" : "disabled"}
                    </Tag>
                  }
                  actions={[
                    <Button
                      key="run"
                      type="primary"
                      size="small"
                      icon={<PlayCircleOutlined />}
                      loading={runningOne === s.source_id}
                      onClick={() => runOne(s.source_id)}
                    >
                      立即运行
                    </Button>,
                  ]}
                >
                  <Space direction="vertical" size={6} style={{ width: "100%" }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      ID: <code>{s.source_id}</code> · 调度: <code>{s.schedule}</code>
                    </Text>
                    <Space>
                      <Badge
                        status={s.last_status === "ok" ? "success" : "default"}
                      />
                      <Text style={{ fontSize: 12 }}>
                        上次:&nbsp;
                        {s.last_run
                          ? `${s.last_run.row_count} 行 @ ${s.last_run.uploaded_at.slice(0, 16).replace("T", " ")}`
                          : "尚未运行"}
                      </Text>
                    </Space>
                    {detail && detail.history.length > 0 ? (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        <HistoryOutlined /> 历史 {detail.history.length} 条
                      </Text>
                    ) : null}
                    <Collapse
                      size="small"
                      ghost
                      items={[
                        {
                          key: "history",
                          label: <Text style={{ fontSize: 12 }}>查看历史</Text>,
                          children:
                            detail && detail.history.length > 0 ? (
                              <Table<ScraperHistoryItem>
                                size="small"
                                rowKey="upload_id"
                                dataSource={detail.history}
                                pagination={false}
                                columns={[
                                  {
                                    title: "抓取时间",
                                    dataIndex: "uploaded_at",
                                    render: (v: string) =>
                                      v.slice(0, 19).replace("T", " "),
                                  },
                                  {
                                    title: "行数",
                                    dataIndex: "row_count",
                                    width: 80,
                                  },
                                ]}
                              />
                            ) : (
                              <Empty
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                description="暂无历史"
                              />
                            ),
                        },
                      ]}
                    />
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}

      {/* ── Last-run results summary ── */}
      {lastRunResults && lastRunResults.length > 0 ? (
        <Card
          size="small"
          title="最近一次运行结果"
          style={{ marginTop: 16 }}
        >
          <Space direction="vertical" size={6} style={{ width: "100%" }}>
            {lastRunResults.map((r) => (
              <Space key={r.source_id} wrap>
                <Tag color={STATUS_COLOR[r.status] ?? "default"}>{r.status}</Tag>
                <Text strong>{r.name}</Text>
                <Text type="secondary">· {r.rows} 行</Text>
                {r.used_fallback ? (
                  <Tag color="orange">已使用 mock 兜底</Tag>
                ) : null}
                {r.error ? (
                  <Text type="warning" style={{ fontSize: 12 }}>
                    {r.error}
                  </Text>
                ) : null}
                {r.upload_id ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    upload_id: <code>{r.upload_id}</code>
                  </Text>
                ) : null}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  耗时: {r.elapsed_ms}ms
                </Text>
              </Space>
            ))}
          </Space>
        </Card>
      ) : null}

      {/* ── 7-day timeline ── */}
      <Card
        size="small"
        title={
          <Space>
            <HistoryOutlined />
            最近 7 天抓取时间线
          </Space>
        }
        style={{ marginTop: 16 }}
      >
        {timeline.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="最近 7 天还没有抓取记录 — 点 [立即全部运行] 试一次"
          />
        ) : (
          <Timeline
            mode="left"
            items={timeline.map((t, idx) => ({
              key: `${t.source}-${t.ts}-${idx}`,
              color: t.status === "ok" ? "green" : "orange",
              label: new Date(t.ts).toLocaleString("zh-CN"),
              children: (
                <Space>
                  <Tag>{t.source}</Tag>
                  <Text>{t.rows} 行</Text>
                </Space>
              ),
            }))}
          />
        )}
      </Card>
    </div>
  );
}
