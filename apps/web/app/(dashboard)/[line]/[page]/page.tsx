// apps/web/app/(dashboard)/[line]/[page]/page.tsx
//
// Dynamic business-line subpage route. Resolves which API endpoint to
// call for a given (line, page) pair via linePageConfig, fetches data,
// and renders with UniversalKpiCard + UniversalChart.
//
// The renderer is data-driven: the only line-specific knowledge lives
// in `getPageSpec()` (linePageConfig.ts). Adding a new (line, page)
// combination requires editing ONLY that table — no new files here.

"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  Card,
  Col,
  Row,
  Skeleton,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { EmptyState, UniversalChart, UniversalKpiCard } from "@biz-bp/ui";
import type { BusinessLine } from "@biz-bp/types";
import {
  buildLinePageConfig,
  getPageSpec,
  isKnownLine,
  setLinePageConfig,
} from "../../_components/linePageConfig";

const { Title, Paragraph } = Typography;

interface RegistryResponse {
  version?: string;
  lines: BusinessLine[];
}

interface DetailItem {
  property_id: string;
  name: string;
  city: string;
  format?: string;
}

interface ProjectItem {
  project_id: string;
  name: string;
  city: string;
}

function displayName(line: BusinessLine): string {
  return line.display_name ?? line.name ?? line.id;
}

function findLineTitle(lineId: string, page: string, lines: BusinessLine[] | undefined): string {
  if (!lines) return page;
  const line = lines.find((l) => l.id === lineId);
  const match = line?.nav?.find((n) => n.path === `/${lineId}/${page}`);
  return match?.title ?? page;
}

export default function LineSubPage() {
  const params = useParams<{ line: string; page: string }>();
  const lineId = (params?.line ?? "").toString();
  const pageSlug = (params?.page ?? "").toString();

  // Pull registry for titles & validation
  const [registry, setRegistry] = useState<RegistryResponse | null>(null);
  const [registryError, setRegistryError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/registry", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as RegistryResponse;
        if (cancelled) return;
        setRegistry(data);
        // P2 #3: build the dynamic page-spec table from the registry
        // so every (line, page) pair declared in `business_lines/<line>/manifest.yaml`
        // is wired into the App Router automatically.
        setLinePageConfig(buildLinePageConfig(data.lines ?? []));
      } catch (e) {
        if (!cancelled) setRegistryError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const known = isKnownLine(lineId);
  const spec = getPageSpec(lineId, pageSlug);
  const line = registry?.lines.find((l) => l.id === lineId);
  const navItem = line?.nav?.find((n) => n.path === `/${lineId}/${pageSlug}`);
  const pageTitle = navItem?.title ?? findLineTitle(lineId, pageSlug, registry?.lines);

  // ── Unknown line id → empty state ──
  if (!known) {
    return (
      <div style={{ padding: 24 }}>
        <EmptyState
          title="未知业务线"
          description={`未在 registry 中找到业务线: ${lineId || "(empty)"}。`}
          docsHref="/docs/plugin-howto.md"
          docsLabel="查看插件编写指南"
        />
      </div>
    );
  }

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          {line ? displayName(line) : lineId} · {pageTitle}
        </Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          路径: <code>/{lineId}/{pageSlug}</code> · 渲染类型: <code>{spec.kind}</code>
        </Paragraph>
      </Space>

      {registryError ? (
        <EmptyState title="无法加载 registry" description={registryError} />
      ) : spec.kind === "not-integrated" ? (
        <NotIntegratedHint lineId={lineId} pageSlug={pageSlug} />
      ) : spec.kind === "line-overview" ? (
        // Defensive: if /[line]/[page]/[empty]? gets routed here somehow,
        // bounce back to the line overview.
        <EmptyState
          title="页面不存在"
          description="该路径不指向具体页面。"
        />
      ) : (
        <PageBody spec={spec} lineId={lineId} pageSlug={pageSlug} />
      )}
    </div>
  );
}

function NotIntegratedHint({ lineId, pageSlug }: { lineId: string; pageSlug: string }) {
  return (
    <EmptyState
      title="该业务线页面尚未接入"
      description={`/${lineId}/${pageSlug} 在 linePageConfig.ts 中没有配置 API 端点。后续接入后,只需在该表中加一行,无需新增 App Router 页面。`}
    />
  );
}

interface PageBodyProps {
  spec: ReturnType<typeof getPageSpec>;
  lineId: string;
  pageSlug: string;
}

/**
 * The body renderer. Switches on the page-spec kind and renders the
 * appropriate universal component(s). All data fetches hit the
 * registry-derived api_prefix — never a hardcoded prefix.
 */
function PageBody({ spec, lineId, pageSlug }: PageBodyProps) {
  // Resolve api_prefix from registry
  const [apiPrefix, setApiPrefix] = useState<string | null>(null);
  const [prefixError, setPrefixError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/registry/lines", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as RegistryResponse;
        if (cancelled) return;
        const line = data.lines.find((l) => l.id === lineId);
        if (!line) throw new Error(`line ${lineId} not in registry`);
        setApiPrefix(line.api_prefix);
      } catch (e) {
        if (!cancelled) setPrefixError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [lineId]);

  if (prefixError) {
    return <EmptyState title="无法解析业务线" description={prefixError} />;
  }
  if (!apiPrefix) {
    return (
      <div style={{ textAlign: "center", padding: 32 }}>
        <Spin tip="加载业务线元数据..." />
      </div>
    );
  }

  if (spec.kind === "property-detail" && spec.detailEndpoint) {
    return (
      <PropertyDetailView
        apiPrefix={apiPrefix}
        detailEndpoint={spec.detailEndpoint}
        lineId={lineId}
        pageSlug={pageSlug}
      />
    );
  }
  if (spec.kind === "project-detail" && spec.detailEndpoint) {
    return (
      <ProjectDetailView
        apiPrefix={apiPrefix}
        detailEndpoint={spec.detailEndpoint}
        lineId={lineId}
        pageSlug={pageSlug}
      />
    );
  }
  if (spec.kind === "market-benchmark") {
    return <MarketBenchmarkView apiPrefix={apiPrefix} lineId={lineId} />;
  }
  if (spec.kind === "vacancy-alerts") {
    return <VacancyAlertsView apiPrefix={apiPrefix} lineId={lineId} />;
  }
  if (spec.kind === "properties-summary") {
    return <PropertiesSummaryView apiPrefix={apiPrefix} lineId={lineId} />;
  }
  return <NotIntegratedHint lineId={lineId} pageSlug={pageSlug} />;
}

/* ─────────────────────────────────────────────────────────────────────
 * Per-kind views
 * ──────────────────────────────────────────────────────────────────── */

/**
 * Pick the first id from a list endpoint (properties OR projects).
 * Returns the id and the full items array.
 */
function useFirstIdAndList(
  apiPrefix: string,
  listPath: "properties" | "projects",
  enabled: boolean,
) {
  const [items, setItems] = useState<(DetailItem | ProjectItem)[]>([]);
  const [firstId, setFirstId] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetch(`${apiPrefix}/${listPath}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const list = (data.items ?? data.projects ?? []) as (DetailItem | ProjectItem)[];
        setItems(list);
        if (list.length > 0) {
          // Both shapes have an id field
          setFirstId(((list[0] as DetailItem).property_id ?? (list[0] as ProjectItem).project_id) ?? null);
        }
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [apiPrefix, listPath, enabled]);

  return { items, firstId, loading, error };
}

function PropertyDetailView({
  apiPrefix,
  detailEndpoint,
  lineId,
  pageSlug,
}: {
  apiPrefix: string;
  detailEndpoint: string;
  lineId: string;
  pageSlug: string;
}) {
  const { items, firstId, loading: listLoading, error: listError } = useFirstIdAndList(
    apiPrefix,
    "properties",
    true,
  );

  const [data, setData] = useState<unknown>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    if (!firstId) return;
    let cancelled = false;
    setDetailLoading(true);
    fetch(
      `${apiPrefix}/properties/${firstId}/${detailEndpoint}`,
      { cache: "no-store" },
    )
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setDetailError(String(e)))
      .finally(() => !cancelled && setDetailLoading(false));
    return () => {
      cancelled = true;
    };
  }, [apiPrefix, firstId, detailEndpoint]);

  if (listError) return <EmptyState title="列表加载失败" description={listError} />;
  if (listLoading || detailLoading)
    return <Skeleton active paragraph={{ rows: 8 }} />;
  if (detailError)
    return <EmptyState title="详情加载失败" description={detailError} />;
  if (!firstId)
    return (
      <EmptyState
        title="暂无可用物业"
        description="该业务线没有任何 property/property_id 可供展示。"
      />
    );

  return (
    <>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space>
          <Typography.Text strong>选择物业:</Typography.Text>
          <DetailPicker items={items} currentId={firstId} kind="property" />
        </Space>
      </Card>
      <PropertyDetailBody
        data={data}
        endpoint={detailEndpoint}
        lineId={lineId}
        pageSlug={pageSlug}
      />
    </>
  );
}

function ProjectDetailView({
  apiPrefix,
  detailEndpoint,
  lineId,
  pageSlug,
}: {
  apiPrefix: string;
  detailEndpoint: string;
  lineId: string;
  pageSlug: string;
}) {
  const { items, firstId, loading: listLoading, error: listError } = useFirstIdAndList(
    apiPrefix,
    "projects",
    true,
  );

  const [data, setData] = useState<unknown>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    if (!firstId) return;
    let cancelled = false;
    setDetailLoading(true);
    fetch(
      `${apiPrefix}/projects/${firstId}/${detailEndpoint}`,
      { cache: "no-store" },
    )
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setDetailError(String(e)))
      .finally(() => !cancelled && setDetailLoading(false));
    return () => {
      cancelled = true;
    };
  }, [apiPrefix, firstId, detailEndpoint]);

  if (listError) return <EmptyState title="项目列表加载失败" description={listError} />;
  if (listLoading || detailLoading)
    return <Skeleton active paragraph={{ rows: 8 }} />;
  if (detailError)
    return <EmptyState title="详情加载失败" description={detailError} />;
  if (!firstId)
    return (
      <EmptyState
        title="暂无可用项目"
        description="该业务线没有任何 project_id 可供展示。"
      />
    );

  return (
    <>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space>
          <Typography.Text strong>选择项目:</Typography.Text>
          <DetailPicker items={items} currentId={firstId} kind="project" />
        </Space>
      </Card>
      <ProjectDetailBody
        data={data}
        endpoint={detailEndpoint}
        lineId={lineId}
        pageSlug={pageSlug}
      />
    </>
  );
}

/* ── Detail pickers ────────────────────────────────────────────────── */

function DetailPicker({
  items,
  currentId,
  kind,
}: {
  items: (DetailItem | ProjectItem)[];
  currentId: string;
  kind: "property" | "project";
}) {
  return (
    <select
      defaultValue={currentId}
      style={{ padding: "4px 12px", border: "1px solid #d9d9d9", borderRadius: 6, minWidth: 280 }}
      onChange={() => {
        // The dynamic router resolves "first id" at fetch time, so we
        // don't actually swap here — this is purely a status indicator.
        // A future iteration could push the chosen id into the URL.
      }}
    >
      {items.map((it) => {
        const id = (it as DetailItem).property_id ?? (it as ProjectItem).project_id;
        return (
          <option key={id} value={id}>
            {it.name} · {it.city}
            {kind === "property" && (it as DetailItem).format
              ? ` · ${(it as DetailItem).format}`
              : ""}
          </option>
        );
      })}
    </select>
  );
}

/* ── Renderers for property-detail endpoints ───────────────────────── */

function PropertyDetailBody({
  data,
  endpoint,
  lineId,
  pageSlug,
}: {
  data: unknown;
  endpoint: string;
  lineId: string;
  pageSlug: string;
}) {
  if (!data || typeof data !== "object") {
    return <EmptyState title="无可显示数据" />;
  }
  const obj = data as Record<string, unknown>;

  // NOI waterfall
  if (endpoint === "noi-waterfall") {
    const items = (obj.items as Array<{ step: string; value_wan: number; type: string }>) ?? [];
    return (
      <>
        <Row gutter={[16, 16]}>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "potential", name: "Potential Gross Rent (万元)", format: "number" }}
              value={
                (items.find((i) => i.type === "start")?.value_wan as number) ?? null
              }
              precision={0}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "egr", name: "Effective Gross Rent (万元)", format: "number" }}
              value={
                (items.find((i) => i.type === "subtotal")?.value_wan as number) ?? null
              }
              precision={0}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "noi", name: "NOI (万元)", format: "number" }}
              value={
                (items.find((i) => i.type === "end")?.value_wan as number) ?? null
              }
              precision={0}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "noi_margin", name: "NOI 率", format: "percent" }}
              value={(obj.noi_margin as number) ?? null}
              precision={2}
            />
          </Col>
        </Row>
        <Title level={4} style={{ marginTop: 24 }}>
          瀑布图
        </Title>
        <UniversalChart
          type="bar"
          data={{
            categories: items.map((i) => i.step),
            values: items.map((i) => i.value_wan),
          }}
          options={{ height: 360 }}
        />
      </>
    );
  }

  // brand-mix
  if (endpoint === "brand-mix") {
    const cats = (obj.categories as Array<{
      category: string;
      brand_count: number;
      area_share: number;
      avg_rent_yuan_per_sqm_per_month: number;
    }>) ?? [];
    return (
      <>
        <Row gutter={[16, 16]}>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "diversity", name: "品牌多样性指数", format: "ratio" }}
              value={(obj.diversity_index as number) ?? null}
              precision={4}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "brands", name: "入驻品牌数", format: "number" }}
              value={(obj.total_brands as number) ?? null}
              precision={0}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "cats", name: "业态数", format: "number" }}
              value={cats.length}
              precision={0}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "leases", name: "采样租约数", format: "number" }}
              value={(obj.sampled_leases as number) ?? null}
              precision={0}
            />
          </Col>
        </Row>
        <Title level={4} style={{ marginTop: 24 }}>
          业态面积占比
        </Title>
        <UniversalChart
          type="bar"
          data={{
            categories: cats.map((c) => c.category),
            values: cats.map((c) => Number((c.area_share * 100).toFixed(2))),
          }}
          options={{ height: 320, yAxisLabel: "面积占比 (%)" }}
        />
      </>
    );
  }

  // renovation-npv
  if (endpoint === "renovation-npv") {
    const m = obj.maintain as { npv_wan: number; irr: number; capex_wan: number } | undefined;
    const r = obj.renovate as { npv_wan: number; irr: number; capex_wan: number } | undefined;
    const delta = obj.delta as { npv_wan: number; delta_label: string } | undefined;
    return (
      <>
        <Row gutter={[16, 16]}>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "m_npv", name: "维持 NPV (万元)", format: "number" }}
              value={m?.npv_wan ?? null}
              precision={0}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "m_irr", name: "维持 IRR", format: "percent" }}
              value={m?.irr ?? null}
              precision={4}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "r_npv", name: "调改 NPV (万元)", format: "number" }}
              value={r?.npv_wan ?? null}
              precision={0}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "r_irr", name: "调改 IRR", format: "percent" }}
              value={r?.irr ?? null}
              precision={4}
            />
          </Col>
        </Row>
        <Title level={4} style={{ marginTop: 24 }}>
          NPV 对比
        </Title>
        <UniversalChart
          type="bar"
          data={{
            categories: ["维持方案", "调改方案", "差额"],
            values: [
              m?.npv_wan ?? 0,
              r?.npv_wan ?? 0,
              delta?.npv_wan ?? 0,
            ],
          }}
          options={{ height: 320, yAxisLabel: "NPV (万元)" }}
        />
        {delta ? (
          <Paragraph type="secondary" style={{ marginTop: 12 }}>
            结论: {delta.delta_label}
          </Paragraph>
        ) : null}
      </>
    );
  }

  // collection-rate
  if (endpoint === "collection-rate") {
    const trend = (obj.trend as Array<{ month: string; rate: number }>) ?? [];
    return (
      <>
        <Row gutter={[16, 16]}>
          <Col xs={12} md={8}>
            <UniversalKpiCard
              indicator={{ id: "cur", name: "当期收缴率", format: "percent" }}
              value={(obj.current_rate as number) ?? null}
              precision={4}
            />
          </Col>
          <Col xs={12} md={8}>
            <UniversalKpiCard
              indicator={{ id: "avg", name: "12 月平均", format: "percent" }}
              value={(obj.average_rate_12m as number) ?? null}
              precision={4}
            />
          </Col>
          <Col xs={12} md={8}>
            <UniversalKpiCard
              indicator={{ id: "worst", name: "最差月", format: "percent" }}
              value={
                (obj.worst_month as { rate: number } | undefined)?.rate ?? null
              }
              precision={4}
            />
          </Col>
        </Row>
        <Title level={4} style={{ marginTop: 24 }}>
          12 个月趋势
        </Title>
        <UniversalChart
          type="line"
          data={{
            categories: trend.map((t) => t.month),
            values: trend.map((t) => t.rate),
          }}
          options={{
            height: 320,
            yAxisLabel: "收缴率",
            echartsOverrides: {
              yAxis: {
                axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
              },
            },
          }}
        />
      </>
    );
  }

  // Unknown endpoint — render the raw JSON
  return (
    <Card size="small" title={`数据: ${endpoint}`}>
      <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
        {JSON.stringify(data, null, 2)}
      </pre>
    </Card>
  );
}

/* ── Renderers for project-detail endpoints ────────────────────────── */

function ProjectDetailBody({
  data,
  endpoint,
}: {
  data: unknown;
  endpoint: string;
  lineId: string;
  pageSlug: string;
}) {
  if (!data || typeof data !== "object") {
    return <EmptyState title="无可显示数据" />;
  }
  const obj = data as Record<string, unknown>;

  // dynamic-pl: KPIs only
  if (endpoint === "dynamic-pl") {
    return (
      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "irr", name: "动态 IRR", format: "percent" }}
            value={(obj.irr as number) ?? null}
            precision={4}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "net_margin", name: "动态净利率", format: "percent" }}
            value={(obj.net_margin as number) ?? null}
            precision={4}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "roi", name: "项目级 ROI", format: "percent" }}
            value={(obj.project_roi as number) ?? null}
            precision={4}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "dedup", name: "月度去化率", format: "percent" }}
            value={(obj.monthly_dedup_rate as number) ?? null}
            precision={4}
          />
        </Col>
      </Row>
    );
  }

  // payment: plan vs actual line chart
  if (endpoint === "payment") {
    const months = (obj.months as string[]) ?? [];
    const plan = (obj.monthly_plan_yi as number[]) ?? [];
    const actual = (obj.monthly_actual_yi as number[]) ?? [];
    return (
      <>
        <Row gutter={[16, 16]}>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "comp", name: "回款完成率", format: "percent" }}
              value={(obj.payment_completion as number) ?? null}
              precision={4}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "vs_plan", name: "月度回款/计划", format: "percent" }}
              value={(obj.payment_vs_plan as number) ?? null}
              precision={4}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "ch_ratio", name: "渠道费占比", format: "percent" }}
              value={(obj.channel_fee_ratio as number) ?? null}
              precision={4}
            />
          </Col>
          <Col xs={12} md={6}>
            <UniversalKpiCard
              indicator={{ id: "cum_actual", name: "累计回款 (亿)", format: "number" }}
              value={(obj.cumulative_actual_yi as number) ?? null}
              precision={2}
            />
          </Col>
        </Row>
        <Title level={4} style={{ marginTop: 24 }}>
          计划 vs 实际
        </Title>
        <UniversalChart
          type="line"
          data={{
            categories: months,
            values: actual,
            series: ["实际回款"],
          }}
          options={{
            height: 320,
            yAxisLabel: "回款 (亿元)",
            echartsOverrides: {
              legend: { data: ["计划回款", "实际回款"], top: 0, right: 0 },
              series: [
                {
                  name: "计划回款",
                  type: "line",
                  data: plan,
                  lineStyle: { type: "dashed", color: "#8c8c8c" },
                },
                {
                  name: "实际回款",
                  type: "line",
                  data: actual,
                  itemStyle: { color: "#1677ff" },
                },
              ],
            },
          }}
        />
      </>
    );
  }

  // redlines: 3 indicator KPIs + thresholds
  if (endpoint === "redlines") {
    const status = obj.status as Record<string, string> | undefined;
    const thresholds = obj.thresholds as Record<string, number> | undefined;
    return (
      <>
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8}>
            <Card size="small" title="资产负债率">
              <Space direction="vertical" size={4}>
                <UniversalKpiCard
                  indicator={{ id: "alr", name: "当前", format: "percent" }}
                  value={(obj.asset_liability_ratio as number) ?? null}
                  precision={4}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  阈值 ≤ {((thresholds?.asset_liability_ratio ?? 0) * 100).toFixed(0)}%
                  {" · "}
                  <Tag color={status?.asset_liability_ratio === "green" ? "green" : "red"}>
                    {status?.asset_liability_ratio === "green" ? "达标" : "踩线"}
                  </Tag>
                </Typography.Text>
              </Space>
            </Card>
          </Col>
          <Col xs={24} md={8}>
            <Card size="small" title="净负债率">
              <Space direction="vertical" size={4}>
                <UniversalKpiCard
                  indicator={{ id: "ndr", name: "当前", format: "percent" }}
                  value={(obj.net_debt_ratio as number) ?? null}
                  precision={4}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  阈值 ≤ {((thresholds?.net_debt_ratio ?? 0) * 100).toFixed(0)}%
                  {" · "}
                  <Tag color={status?.net_debt_ratio === "green" ? "green" : "red"}>
                    {status?.net_debt_ratio === "green" ? "达标" : "踩线"}
                  </Tag>
                </Typography.Text>
              </Space>
            </Card>
          </Col>
          <Col xs={24} md={8}>
            <Card size="small" title="现金短债比">
              <Space direction="vertical" size={4}>
                <UniversalKpiCard
                  indicator={{ id: "csd", name: "当前", format: "ratio" }}
                  value={(obj.cash_to_short_debt as number) ?? null}
                  precision={2}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  阈值 ≥ {(thresholds?.cash_to_short_debt ?? 0).toFixed(2)}x
                  {" · "}
                  <Tag color={status?.cash_to_short_debt === "green" ? "green" : "red"}>
                    {status?.cash_to_short_debt === "green" ? "达标" : "踩线"}
                  </Tag>
                </Typography.Text>
              </Space>
            </Card>
          </Col>
        </Row>
      </>
    );
  }

  // dedup-forecast: history + forecast
  if (endpoint === "dedup-forecast") {
    const historyMonths = (obj.history_months as string[]) ?? [];
    const forecastMonths = (obj.forecast_months as string[]) ?? [];
    const all = [...historyMonths, ...forecastMonths];
    const median = (obj.forecast_median as number[]) ?? [];
    return (
      <>
        <Title level={4}>历史 + 预测</Title>
        <UniversalChart
          type="line"
          data={{
            categories: all,
            values: [
              ...new Array(historyMonths.length).fill(null),
              ...median,
            ],
          }}
          options={{
            height: 360,
            yAxisLabel: "去化率",
            echartsOverrides: {
              yAxis: {
                axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
              },
            },
          }}
        />
      </>
    );
  }

  return (
    <Card size="small" title={`数据: ${endpoint}`}>
      <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
        {JSON.stringify(data, null, 2)}
      </pre>
    </Card>
  );
}

/* ── market-benchmark (retail-leasing/market-report) ───────────────── */

function MarketBenchmarkView({
  apiPrefix,
  lineId,
}: {
  apiPrefix: string;
  lineId: string;
}) {
  const [data, setData] = useState<{
    items?: Array<{
      property_name: string;
      benchmark_gap_pct: number;
      comparable_count: number;
      deal_rent: number;
      city: string;
    }>;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${apiPrefix}/market-benchmark`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [apiPrefix]);

  if (loading) return <Skeleton active paragraph={{ rows: 6 }} />;
  if (error) return <EmptyState title="加载失败" description={error} />;
  const items = data?.items ?? [];

  return (
    <>
      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "props", name: "在管商铺", format: "number" }}
            value={items.length}
            precision={0}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "above", name: "高于基准", format: "number" }}
            value={items.filter((i) => i.benchmark_gap_pct > 0).length}
            precision={0}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "below", name: "低于基准", format: "number" }}
            value={items.filter((i) => i.benchmark_gap_pct <= 0).length}
            precision={0}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "avg_gap", name: "平均对标差", format: "percent" }}
            value={
              items.length > 0
                ? items.reduce((s, i) => s + i.benchmark_gap_pct, 0) / items.length
                : 0
            }
            precision={4}
          />
        </Col>
      </Row>
      <Title level={4} style={{ marginTop: 24 }}>
        基准对标差
      </Title>
      <UniversalChart
        type="bar"
        data={{
          categories: items.map((i) => i.property_name),
          values: items.map((i) => Number((i.benchmark_gap_pct * 100).toFixed(2))),
        }}
        options={{
          height: 320,
          yAxisLabel: "对标差 (%)",
        }}
      />
    </>
  );
}

/* ── vacancy-alerts (retail-leasing/vacancy-alert) ─────────────────── */

function VacancyAlertsView({
  apiPrefix,
  lineId,
}: {
  apiPrefix: string;
  lineId: string;
}) {
  const [data, setData] = useState<{
    alerts?: Array<{
      owner: string;
      severity: "high" | "medium" | "low";
      max_vacancy_days: number;
      worst_property: string;
      property_count: number;
    }>;
    threshold_days?: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${apiPrefix}/vacancy-alerts`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [apiPrefix]);

  if (loading) return <Skeleton active paragraph={{ rows: 6 }} />;
  if (error) return <EmptyState title="加载失败" description={error} />;
  const alerts = data?.alerts ?? [];
  const high = alerts.filter((a) => a.severity === "high").length;
  const med = alerts.filter((a) => a.severity === "medium").length;

  return (
    <>
      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "alert", name: "预警合计", format: "number" }}
            value={alerts.length}
            precision={0}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "high", name: "高风险", format: "number" }}
            value={high}
            precision={0}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "med", name: "中风险", format: "number" }}
            value={med}
            precision={0}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "th", name: "阈值 (天)", format: "number" }}
            value={data?.threshold_days ?? 60}
            precision={0}
          />
        </Col>
      </Row>
      <Title level={4} style={{ marginTop: 24 }}>
        各业主最大空置期
      </Title>
      <UniversalChart
        type="bar"
        data={{
          categories: alerts.map((a) => a.owner),
          values: alerts.map((a) => a.max_vacancy_days),
        }}
        options={{ height: 320, yAxisLabel: "天数" }}
      />
      <Title level={4} style={{ marginTop: 24 }}>
        预警明细
      </Title>
      <Table
        size="small"
        rowKey="owner"
        dataSource={alerts}
        pagination={false}
        columns={[
          { title: "业主", dataIndex: "owner", width: 200 },
          {
            title: "严重度",
            dataIndex: "severity",
            width: 100,
            render: (v: string) => (
              <Tag color={v === "high" ? "red" : v === "medium" ? "orange" : "green"}>
                {v === "high" ? "高" : v === "medium" ? "中" : "低"}
              </Tag>
            ),
          },
          { title: "最大空置期 (天)", dataIndex: "max_vacancy_days", width: 130 },
          { title: "最差物业", dataIndex: "worst_property" },
          { title: "关联商铺数", dataIndex: "property_count", width: 110 },
        ]}
      />
    </>
  );
}

/* ── properties-summary (retail-leasing/leasing-kpi) ──────────────── */

function PropertiesSummaryView({
  apiPrefix,
  lineId,
}: {
  apiPrefix: string;
  lineId: string;
}) {
  const [data, setData] = useState<{
    items?: Array<{
      name: string;
      owner: string;
      city: string;
      headline_kpis: {
        occupancy_rate: number;
        avg_deal_rent: number;
        benchmark_gap_pct: number;
        owner_vacancy_days: number;
        commission_revenue: number;
        renewal_rate: number;
        brand_entry_rate: number;
        quarterly_market_reports: number;
      };
    }>;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${apiPrefix}/properties`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [apiPrefix]);

  if (loading) return <Skeleton active paragraph={{ rows: 6 }} />;
  if (error) return <EmptyState title="加载失败" description={error} />;
  const items = data?.items ?? [];
  const n = Math.max(items.length, 1);
  const avg = (sel: (it: (typeof items)[number]) => number): number =>
    items.length === 0 ? 0 : items.reduce((s, it) => s + sel(it), 0) / n;
  const occupancy = avg((it) => it.headline_kpis.occupancy_rate);
  const dealRent = avg((it) => it.headline_kpis.avg_deal_rent);
  const gap = avg((it) => it.headline_kpis.benchmark_gap_pct);
  const vacancyDays = avg((it) => it.headline_kpis.owner_vacancy_days);
  const totalReports = items.reduce(
    (s, it) => s + (it.headline_kpis.quarterly_market_reports ?? 0),
    0,
  );
  const brandEntry = avg((it) => it.headline_kpis.brand_entry_rate);
  const renewal = avg((it) => it.headline_kpis.renewal_rate);
  const commission = items.reduce(
    (s, it) => s + (it.headline_kpis.commission_revenue ?? 0),
    0,
  );

  return (
    <>
      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "occ", name: "出租率", format: "percent" }}
            value={occupancy}
            precision={2}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "rent", name: "平均成交租金", format: "currency", unit: "元/㎡/月" }}
            value={dealRent}
            precision={0}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "gap", name: "基准对标差", format: "percent" }}
            value={gap}
            precision={2}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "vd", name: "业主空置期", format: "number", unit: "天" }}
            value={vacancyDays}
            precision={0}
          />
        </Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "qr", name: "季度报告", format: "number", unit: "份" }}
            value={totalReports}
            precision={0}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "be", name: "品牌入驻率", format: "percent" }}
            value={brandEntry}
            precision={2}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "rn", name: "续约率", format: "percent" }}
            value={renewal}
            precision={2}
          />
        </Col>
        <Col xs={12} md={6}>
          <UniversalKpiCard
            indicator={{ id: "comm", name: "佣金收入", format: "currency", unit: "万元" }}
            value={commission}
            precision={1}
          />
        </Col>
      </Row>
    </>
  );
}


