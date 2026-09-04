// apps/web/app/(dashboard)/admin/business-lines/[id]/page.tsx
//
// Admin → 业务线编辑页 (D2, 2026-09-04).
//
// 5 个区块, 用 antd Tabs 顺序展示:
//   1. v1 基础        — id/name/version/owner/icon/description/api_prefix/
//                        warehouse/refresh/features/nav
//   2. v2 数据域      — 5 域 Checkbox (data_scope.domains)
//   3. v2 角色分配    — owner_role_assignments 三组 "<role>:<line_id>"
//   4. v2 访问矩阵    — 4 角色 × 5 域 Checkbox
//   5. v2 KPI         — fin_view / hr_view / shared_view 三栏, 每栏 add/remove
//
// 保存: PATCH /api/admin/business-lines/{id}, 成功 toast + 跳回列表页.
// 错误: 400/422/404 走错误卡片 + 列表渲染; 502 (BFF 转发失败) 走 toast.
//
// UX 关键决策:
//   - 默认打开第 1 个 Tab "v1 基础" — 名字 / owner / description 是最高频改动
//   - 编辑态 vs 受控态混用: 顶层 v1 字段走 useState(对象), 列表 (nav, kpis)
//     走 useState(数组) + 不可变更新, 避免深拷贝 boilerplate
//   - 保存按钮只在脏(dirty)时高亮, 未改时禁用
//   - 离开页面前不拦截 (admin 是低频操作, 数据丢失风险可接受; 显式 modal
//     反而干扰 round-trip 编辑)

"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  PlusOutlined,
  SaveOutlined,
  ReloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import type {
  BusinessLineFull,
  BusinessLineKpiItem,
  BusinessLineNavItem,
  UpdateBusinessLinePayload,
  V2AccessRole,
  V2DataDomain,
} from "@biz-bp/types";

import {
  accessRoleColor,
  accessRoleLabel,
  DEFAULT_ACCESS_MATRIX,
  domainColor,
  domainLabel,
  getBusinessLine,
  isValidRoleBinding,
  kpiViewLabel,
  updateBusinessLine,
  V2_ACCESS_ROLES,
  V2_DOMAINS,
  V2_KPI_VIEWS,
  type V2KpiViewKey,
} from "../../../../../lib/business-lines";

const { Title, Text, Paragraph } = Typography;

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// ---------------------------------------------------------------------------
// Editable draft. We start from a deep copy of the server's
// BusinessLineFull so mutations don't leak back into the loaded record.
// ---------------------------------------------------------------------------

interface DraftKpi extends BusinessLineKpiItem {
  id: string;
  title: string;
  source?: string;
  formula?: string;
}

interface Draft {
  // v1
  name: string;
  version: string;
  description: string;
  owner: string;
  icon: string;
  api_prefix: string;
  warehouse: { schema: string; dbt_schema: string; mart_schema: string };
  refresh: { schedule: string; enabled: boolean };
  features: { universal_kpi: boolean; universal_chart: boolean; ag_grid: boolean };
  nav: BusinessLineNavItem[];
  // v2
  domains: V2DataDomain[];
  owner_role_assignments: { finance_bp: string; hr_bp: string; line_owner: string };
  access_matrix: Record<string, V2DataDomain[]>;
  kpis: Record<V2KpiViewKey, DraftKpi[]>;
}

function toDraft(b: BusinessLineFull): Draft {
  return {
    name: b.name ?? "",
    version: b.version ?? "0.0.0",
    description: b.description ?? "",
    owner: b.owner ?? "",
    icon: b.icon ?? "AppstoreOutlined",
    api_prefix: b.api_prefix ?? "",
    warehouse: {
      schema: b.warehouse?.schema ?? "",
      dbt_schema: b.warehouse?.dbt_schema ?? "",
      mart_schema: b.warehouse?.mart_schema ?? "",
    },
    refresh: {
      schedule: b.refresh?.schedule ?? "0 2 * * *",
      enabled: b.refresh?.enabled ?? true,
    },
    features: {
      universal_kpi: b.features?.universal_kpi ?? true,
      universal_chart: b.features?.universal_chart ?? true,
      ag_grid: b.features?.ag_grid ?? true,
    },
    nav: (b.nav ?? []).map((n) => ({ path: n.path, title: n.title })),
    // v2
    domains: (b.data_scope?.domains ?? [...V2_DOMAINS]) as V2DataDomain[],
    owner_role_assignments: {
      finance_bp: b.owner_role_assignments?.finance_bp ?? "",
      hr_bp: b.owner_role_assignments?.hr_bp ?? "",
      line_owner: b.owner_role_assignments?.line_owner ?? "",
    },
    access_matrix: {
      fin_bp: (b.access_matrix?.fin_bp ??
        (DEFAULT_ACCESS_MATRIX.fin_bp as V2DataDomain[])),
      hr_bp: (b.access_matrix?.hr_bp ??
        (DEFAULT_ACCESS_MATRIX.hr_bp as V2DataDomain[])),
      line_owner: (b.access_matrix?.line_owner ??
        (DEFAULT_ACCESS_MATRIX.line_owner as V2DataDomain[])),
      line_member: (b.access_matrix?.line_member ??
        (DEFAULT_ACCESS_MATRIX.line_member as V2DataDomain[])),
    },
    kpis: {
      fin_view: (b.kpis?.fin_view ?? []).map((k) => ({ ...k })),
      hr_view: (b.kpis?.hr_view ?? []).map((k) => ({ ...k })),
      shared_view: (b.kpis?.shared_view ?? []).map((k) => ({ ...k })),
    },
  };
}

/** True iff the draft differs from the original snapshot. */
function isDirty(a: Draft, b: Draft): boolean {
  return JSON.stringify(a) !== JSON.stringify(b);
}

/** Project the working draft back into the PATCH payload. */
function toPayload(d: Draft): UpdateBusinessLinePayload {
  return {
    name: d.name,
    description: d.description,
    owner: d.owner,
    icon: d.icon,
    api_prefix: d.api_prefix,
    data_scope: { domains: d.domains },
    owner_role_assignments: {
      finance_bp: d.owner_role_assignments.finance_bp || undefined,
      hr_bp: d.owner_role_assignments.hr_bp || undefined,
      line_owner: d.owner_role_assignments.line_owner || undefined,
    },
    access_matrix: {
      fin_bp: d.access_matrix.fin_bp,
      hr_bp: d.access_matrix.hr_bp,
      line_owner: d.access_matrix.line_owner,
      line_member: d.access_matrix.line_member,
    },
    kpis: {
      fin_view: d.kpis.fin_view.map(({ id, title, source, formula }) => ({
        id,
        title,
        source,
        formula,
      })),
      hr_view: d.kpis.hr_view.map(({ id, title, source, formula }) => ({
        id,
        title,
        source,
        formula,
      })),
      shared_view: d.kpis.shared_view.map(({ id, title, source, formula }) => ({
        id,
        title,
        source,
        formula,
      })),
    },
  };
}

// ---------------------------------------------------------------------------
// Reusable: domain checkbox group (used in 区块 2)
// ---------------------------------------------------------------------------

function DomainCheckboxGroup({
  value,
  onChange,
}: {
  value: V2DataDomain[];
  onChange: (next: V2DataDomain[]) => void;
}) {
  return (
    <Checkbox.Group
      value={value}
      onChange={(vals) => onChange(vals as V2DataDomain[])}
    >
      <Space size={[12, 8]} wrap>
        {V2_DOMAINS.map((d) => (
          <Checkbox key={d} value={d} aria-label={d}>
            <Tag color={domainColor(d)} style={{ marginRight: 4 }}>
              {domainLabel(d)}
            </Tag>
          </Checkbox>
        ))}
      </Space>
    </Checkbox.Group>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function AdminBusinessLineEditPage() {
  const params = useParams<{ id: string }>();
  const lineId = decodeURIComponent(params.id ?? "");
  const router = useRouter();
  const { message } = App.useApp();

  const [original, setOriginal] = useState<Draft | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);

  // --------------------------------------------------------------- Loaders
  const load = useCallback(async () => {
    if (!lineId) return;
    setLoading(true);
    setLoadError(null);
    setValidationErrors([]);
    try {
      const data = await getBusinessLine(lineId);
      const d = toDraft(data);
      setOriginal(d);
      setDraft(d);
    } catch (e) {
      const err = e as Error & { status?: number };
      if (err.status === 404) {
        setLoadError(`业务线不存在: ${lineId}`);
      } else {
        setLoadError(getErrorMessage(e));
      }
      setOriginal(null);
      setDraft(null);
    } finally {
      setLoading(false);
    }
  }, [lineId]);

  useEffect(() => {
    load();
  }, [load]);

  const dirty = useMemo(
    () => (original && draft ? isDirty(original, draft) : false),
    [original, draft],
  );

  // --------------------------------------------------------------- Mutators
  const setField = <K extends keyof Draft>(k: K, v: Draft[K]) => {
    setDraft((d) => (d ? { ...d, [k]: v } : d));
  };

  // --------------------------------------------------------------- Save
  const handleSave = async () => {
    if (!draft || !dirty) return;
    setSaving(true);
    setValidationErrors([]);
    try {
      await updateBusinessLine(lineId, toPayload(draft));
      message.success(`已保存 ${lineId}`);
      router.push("/admin/business-lines");
    } catch (e) {
      const err = e as Error & { status?: number };
      if (err.status === 422) {
        // FastAPI 422: detail is an array of {loc, msg, type}
        const lines = err.message.split("\n").filter(Boolean);
        setValidationErrors(lines);
        message.error(`校验失败: ${lines.length} 条错误,请查看下方详情`);
      } else if (err.status === 404) {
        message.error("业务线不存在,可能已被删除");
        setLoadError("业务线不存在");
      } else {
        message.error(`保存失败: ${getErrorMessage(e)}`);
      }
    } finally {
      setSaving(false);
    }
  };

  // --------------------------------------------------------------- Render
  if (loading) {
    return (
      <div
        style={{
          padding: 48,
          textAlign: "center",
        }}
        role="status"
        aria-label="加载中"
      >
        <Spin tip="加载业务线 manifest 中..." />
      </div>
    );
  }

  if (loadError || !draft || !original) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <Card bordered={false}>
          <Space style={{ marginBottom: 12 }}>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => router.push("/admin/business-lines")}
            >
              返回业务线列表
            </Button>
          </Space>
          <Alert
            type="error"
            showIcon
            message="无法加载业务线"
            description={loadError ?? "未知错误"}
            action={
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={load}
                aria-label="重试"
              >
                重试
              </Button>
            }
          />
        </Card>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* ============================================================ */}
      {/* Header                                                        */}
      {/* ============================================================ */}
      <Card
        bordered={false}
        styles={{ body: { padding: 16 } }}
        title={
          <Space size="middle">
            <Title level={4} style={{ margin: 0 }}>
              编辑业务线 — <code>{lineId}</code>
            </Title>
            {dirty && (
              <Tag color="orange" data-testid="dirty-indicator">
                有未保存的修改
              </Tag>
            )}
            {!dirty && (
              <Tag color="green" data-testid="clean-indicator">
                已保存
              </Tag>
            )}
          </Space>
        }
        extra={
          <Space>
            <Link href="/admin/business-lines">
              <Button icon={<ArrowLeftOutlined />} aria-label="取消并返回">
                取消
              </Button>
            </Link>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handleSave}
              loading={saving}
              disabled={!dirty}
              aria-label="保存"
            >
              保存
            </Button>
          </Space>
        }
      >
        <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
          保存时后端会原子写回 <code>business_lines/{lineId}/manifest.yaml</code>
          , 写前自动生成 <code>manifest.yaml.bak</code> (单步回滚).
          改 <code>api_prefix</code> 会影响所有前端调用, 请谨慎.
        </Paragraph>
        {validationErrors.length > 0 && (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            message={`校验失败 (${validationErrors.length} 条)`}
            description={
              <ul style={{ marginBottom: 0, paddingLeft: 18 }}>
                {validationErrors.map((line, i) => (
                  <li key={i} style={{ fontSize: 12 }}>
                    {line}
                  </li>
                ))}
              </ul>
            }
          />
        )}
      </Card>

      {/* ============================================================ */}
      {/* 5 区块 — 顺序: v1 基础 → v2 数据域 → v2 角色 → v2 矩阵 → v2 KPI */}
      {/* ============================================================ */}
      <Tabs
        defaultActiveKey="v1"
        items={[
          // -------- 区块 1: v1 基础 --------
          {
            key: "v1",
            label: "v1 基础",
            children: (
              <Card bordered={false} styles={{ body: { padding: 16 } }}>
                <Form layout="vertical">
                  <Form.Item label="ID (不可改)">
                    <Input value={lineId} disabled aria-label="ID" />
                  </Form.Item>
                  <Form.Item label="名称 (name)">
                    <Input
                      value={draft.name}
                      onChange={(e) => setField("name", e.target.value)}
                      placeholder="例: 地产项目管理部"
                      aria-label="name"
                    />
                  </Form.Item>
                  <Form.Item label="版本 (version)">
                    <Input
                      value={draft.version}
                      onChange={(e) => setField("version", e.target.value)}
                      placeholder="例: 0.1.0"
                      aria-label="version"
                    />
                  </Form.Item>
                  <Form.Item label="Owner 邮箱">
                    <Input
                      value={draft.owner}
                      onChange={(e) => setField("owner", e.target.value)}
                      placeholder="例: pm-bp@fin-bp-portal.local"
                      aria-label="owner"
                    />
                  </Form.Item>
                  <Form.Item
                    label="图标 (icon)"
                    tooltip="antd icon 名, 例 ToolOutlined / HomeOutlined"
                  >
                    <Input
                      value={draft.icon}
                      onChange={(e) => setField("icon", e.target.value)}
                      placeholder="例: ToolOutlined"
                      aria-label="icon"
                    />
                  </Form.Item>
                  <Form.Item label="描述 (description)">
                    <Input.TextArea
                      value={draft.description}
                      onChange={(e) =>
                        setField("description", e.target.value)
                      }
                      rows={3}
                      aria-label="description"
                    />
                  </Form.Item>
                  <Form.Item
                    label={
                      <Space size="small">
                        API 路径前缀 (api_prefix)
                        <Tag color="orange" icon={<WarningOutlined />}>
                          危险
                        </Tag>
                      </Space>
                    }
                    tooltip="改这个会影响所有前端调用, 默认 /api/lines/<id>"
                  >
                    <Input
                      value={draft.api_prefix}
                      onChange={(e) =>
                        setField("api_prefix", e.target.value)
                      }
                      placeholder="/api/lines/<id>"
                      aria-label="api_prefix"
                    />
                  </Form.Item>
                </Form>

                <Title level={5} style={{ marginTop: 8 }}>
                  数据仓库 (warehouse)
                </Title>
                <Form layout="vertical">
                  <Space.Compact style={{ width: "100%" }}>
                    <Form.Item
                      label="schema"
                      style={{ flex: 1, marginRight: 8 }}
                    >
                      <Input
                        value={draft.warehouse.schema}
                        onChange={(e) =>
                          setField("warehouse", {
                            ...draft.warehouse,
                            schema: e.target.value,
                          })
                        }
                        placeholder="raw_<line>"
                        aria-label="schema"
                      />
                    </Form.Item>
                    <Form.Item
                      label="dbt_schema"
                      style={{ flex: 1, marginRight: 8 }}
                    >
                      <Input
                        value={draft.warehouse.dbt_schema}
                        onChange={(e) =>
                          setField("warehouse", {
                            ...draft.warehouse,
                            dbt_schema: e.target.value,
                          })
                        }
                        placeholder="stg_<line>"
                        aria-label="dbt_schema"
                      />
                    </Form.Item>
                    <Form.Item label="mart_schema" style={{ flex: 1 }}>
                      <Input
                        value={draft.warehouse.mart_schema}
                        onChange={(e) =>
                          setField("warehouse", {
                            ...draft.warehouse,
                            mart_schema: e.target.value,
                          })
                        }
                        placeholder="mart_<line>"
                        aria-label="mart_schema"
                      />
                    </Form.Item>
                  </Space.Compact>
                </Form>

                <Title level={5} style={{ marginTop: 8 }}>
                  刷新策略 (refresh)
                </Title>
                <Form layout="vertical">
                  <Space style={{ width: "100%" }} size="middle">
                    <Form.Item
                      label="cron schedule"
                      style={{ flex: 1, marginBottom: 0 }}
                    >
                      <Input
                        value={draft.refresh.schedule}
                        onChange={(e) =>
                          setField("refresh", {
                            ...draft.refresh,
                            schedule: e.target.value,
                          })
                        }
                        placeholder="0 2 * * *"
                        aria-label="refresh.schedule"
                      />
                    </Form.Item>
                    <Form.Item
                      label="enabled"
                      style={{ marginBottom: 0 }}
                    >
                      <Switch
                        checked={draft.refresh.enabled}
                        onChange={(v) =>
                          setField("refresh", {
                            ...draft.refresh,
                            enabled: v,
                          })
                        }
                        checkedChildren="启用"
                        unCheckedChildren="停用"
                        aria-label="refresh.enabled"
                      />
                    </Form.Item>
                  </Space>
                </Form>

                <Title level={5} style={{ marginTop: 8 }}>
                  特性开关 (features)
                </Title>
                <Space size="large" wrap>
                  <FeatureSwitch
                    label="universal_kpi"
                    checked={draft.features.universal_kpi}
                    onChange={(v) =>
                      setField("features", {
                        ...draft.features,
                        universal_kpi: v,
                      })
                    }
                  />
                  <FeatureSwitch
                    label="universal_chart"
                    checked={draft.features.universal_chart}
                    onChange={(v) =>
                      setField("features", {
                        ...draft.features,
                        universal_chart: v,
                      })
                    }
                  />
                  <FeatureSwitch
                    label="ag_grid"
                    checked={draft.features.ag_grid}
                    onChange={(v) =>
                      setField("features", {
                        ...draft.features,
                        ag_grid: v,
                      })
                    }
                  />
                </Space>

                <Title level={5} style={{ marginTop: 16 }}>
                  导航 (nav)
                </Title>
                <NavEditor
                  value={draft.nav}
                  onChange={(v) => setField("nav", v)}
                />
              </Card>
            ),
          },

          // -------- 区块 2: v2 数据域 --------
          {
            key: "v2-scope",
            label: "v2 数据域",
            children: (
              <Card bordered={false} styles={{ body: { padding: 16 } }}>
                <Title level={5}>data_scope.domains</Title>
                <Paragraph type="secondary" style={{ fontSize: 12 }}>
                  勾选本业务线涉及的数据域, 用于 v2 RBAC 的&ldquo;按域隔离&rdquo;。
                  未勾选的域 = 该角色看不到。
                </Paragraph>
                <DomainCheckboxGroup
                  value={draft.domains}
                  onChange={(v) => setField("domains", v)}
                />
                <Paragraph
                  type="secondary"
                  style={{ marginTop: 16, fontSize: 12 }}
                >
                  注意: 修改数据域仅影响 v2 RBAC 的&ldquo;可访问范围&rdquo;, 不会改变
                  <code>access_matrix</code> (访问矩阵独立维护)。
                </Paragraph>
              </Card>
            ),
          },

          // -------- 区块 3: v2 角色分配 (owner_role_assignments) --------
          {
            key: "v2-roles",
            label: "v2 角色分配",
            children: (
              <Card bordered={false} styles={{ body: { padding: 16 } }}>
                <Title level={5}>owner_role_assignments</Title>
                <Paragraph type="secondary" style={{ fontSize: 12 }}>
                  三个&ldquo;<code>&lt;role&gt;:&lt;line_id&gt;</code>&rdquo;字符串,
                  是给 admin UI 的提示。 实际 user→role 映射在 DB
                  <code>user_roles</code> 表中。
                </Paragraph>
                <Form layout="vertical" style={{ maxWidth: 600 }}>
                  <Form.Item
                    label="finance_bp"
                    extra={
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        格式: <code>&lt;role_id&gt;:&lt;line_id&gt;</code>,
                        例 <code>fin_bp:project-management</code>
                      </Text>
                    }
                    validateStatus={
                      draft.owner_role_assignments.finance_bp &&
                      !isValidRoleBinding(draft.owner_role_assignments.finance_bp)
                        ? "error"
                        : undefined
                    }
                    help={
                      draft.owner_role_assignments.finance_bp &&
                      !isValidRoleBinding(draft.owner_role_assignments.finance_bp)
                        ? "格式必须为 <role>:<line_id>, role 仅允许 fin_bp / hr_bp / line_owner"
                        : undefined
                    }
                  >
                    <Input
                      value={draft.owner_role_assignments.finance_bp}
                      onChange={(e) =>
                        setField("owner_role_assignments", {
                          ...draft.owner_role_assignments,
                          finance_bp: e.target.value,
                        })
                      }
                      placeholder="fin_bp:project-management"
                      aria-label="finance_bp"
                    />
                  </Form.Item>
                  <Form.Item
                    label="hr_bp"
                    extra={
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        格式: <code>&lt;role_id&gt;:&lt;line_id&gt;</code>,
                        例 <code>hr_bp:project-management</code>
                      </Text>
                    }
                    validateStatus={
                      draft.owner_role_assignments.hr_bp &&
                      !isValidRoleBinding(draft.owner_role_assignments.hr_bp)
                        ? "error"
                        : undefined
                    }
                    help={
                      draft.owner_role_assignments.hr_bp &&
                      !isValidRoleBinding(draft.owner_role_assignments.hr_bp)
                        ? "格式必须为 <role>:<line_id>"
                        : undefined
                    }
                  >
                    <Input
                      value={draft.owner_role_assignments.hr_bp}
                      onChange={(e) =>
                        setField("owner_role_assignments", {
                          ...draft.owner_role_assignments,
                          hr_bp: e.target.value,
                        })
                      }
                      placeholder="hr_bp:project-management"
                      aria-label="hr_bp"
                    />
                  </Form.Item>
                  <Form.Item
                    label="line_owner"
                    extra={
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        格式: <code>&lt;role_id&gt;:&lt;line_id&gt;</code>,
                        例 <code>line_owner:project-management</code>
                      </Text>
                    }
                    validateStatus={
                      draft.owner_role_assignments.line_owner &&
                      !isValidRoleBinding(
                        draft.owner_role_assignments.line_owner,
                      )
                        ? "error"
                        : undefined
                    }
                    help={
                      draft.owner_role_assignments.line_owner &&
                      !isValidRoleBinding(
                        draft.owner_role_assignments.line_owner,
                      )
                        ? "格式必须为 <role>:<line_id>"
                        : undefined
                    }
                  >
                    <Input
                      value={draft.owner_role_assignments.line_owner}
                      onChange={(e) =>
                        setField("owner_role_assignments", {
                          ...draft.owner_role_assignments,
                          line_owner: e.target.value,
                        })
                      }
                      placeholder="line_owner:project-management"
                      aria-label="line_owner"
                    />
                  </Form.Item>
                </Form>
              </Card>
            ),
          },

          // -------- 区块 4: v2 访问矩阵 (access_matrix) --------
          {
            key: "v2-matrix",
            label: "v2 访问矩阵",
            children: (
              <Card bordered={false} styles={{ body: { padding: 16 } }}>
                <Title level={5}>access_matrix</Title>
                <Paragraph type="secondary" style={{ fontSize: 12 }}>
                  4 个 line-scoped 角色 × 5 个数据域的勾选矩阵。
                  全局角色 (admin/auditor/viewer/fin_bp_global/hr_bp_global)
                  不在此处配置, 始终全开。
                </Paragraph>
                <AccessMatrixEditor
                  value={draft.access_matrix}
                  onChange={(v) => setField("access_matrix", v)}
                />
                <Button
                  size="small"
                  style={{ marginTop: 12 }}
                  onClick={() =>
                    setField("access_matrix", {
                      fin_bp: [...DEFAULT_ACCESS_MATRIX.fin_bp] as V2DataDomain[],
                      hr_bp: [...DEFAULT_ACCESS_MATRIX.hr_bp] as V2DataDomain[],
                      line_owner: [...DEFAULT_ACCESS_MATRIX.line_owner] as V2DataDomain[],
                      line_member: [...DEFAULT_ACCESS_MATRIX.line_member] as V2DataDomain[],
                    })
                  }
                  aria-label="重置为默认"
                >
                  重置为 v2 默认值
                </Button>
              </Card>
            ),
          },

          // -------- 区块 5: v2 KPI --------
          {
            key: "v2-kpis",
            label: "v2 KPI",
            children: (
              <Card bordered={false} styles={{ body: { padding: 16 } }}>
                <Title level={5}>kpis</Title>
                <Paragraph type="secondary" style={{ fontSize: 12 }}>
                  三类视角的 KPI 列表。 KPI id 必须 url-safe
                  (<code>[a-z0-9_-]</code>), 后端会 422 校验失败。
                </Paragraph>
                <KpiEditor
                  value={draft.kpis}
                  onChange={(v) => setField("kpis", v)}
                />
              </Card>
            ),
          },
        ]}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FeatureSwitch({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <Space>
      <Text>{label}</Text>
      <Switch
        checked={checked}
        onChange={onChange}
        checkedChildren="on"
        unCheckedChildren="off"
        aria-label={label}
      />
    </Space>
  );
}

function NavEditor({
  value,
  onChange,
}: {
  value: BusinessLineNavItem[];
  onChange: (next: BusinessLineNavItem[]) => void;
}) {
  const [addOpen, setAddOpen] = useState(false);
  const [addForm] = Form.useForm<{ path: string; title: string }>();

  const columns: ColumnsType<BusinessLineNavItem & { __idx: number }> = [
    {
      title: "path",
      dataIndex: "path",
      render: (v: string) => <code>{v}</code>,
    },
    { title: "title", dataIndex: "title" },
    {
      title: "操作",
      key: "actions",
      width: 100,
      render: (_, row) => (
        <Button
          size="small"
          danger
          icon={<DeleteOutlined />}
          onClick={() =>
            onChange(value.filter((_, i) => i !== row.__idx))
          }
          aria-label="删除 nav 项"
        >
          删除
        </Button>
      ),
    },
  ];

  return (
    <>
      <Table
        rowKey={(_, idx) => `nav-${idx ?? 0}`}
        size="small"
        pagination={false}
        dataSource={value.map((n, i) => ({ ...n, __idx: i }))}
        columns={columns}
        locale={{ emptyText: <Empty description="暂无 nav 项" /> }}
      />
      <Button
        type="dashed"
        size="small"
        icon={<PlusOutlined />}
        onClick={() => {
          addForm.resetFields();
          setAddOpen(true);
        }}
        style={{ marginTop: 8 }}
        aria-label="添加 nav 项"
      >
        添加 nav 项
      </Button>
      <Modal
        title="添加 nav 项"
        open={addOpen}
        onCancel={() => setAddOpen(false)}
        onOk={async () => {
          const v = await addForm.validateFields();
          onChange([...value, { path: v.path, title: v.title }]);
          setAddOpen(false);
        }}
        okText="添加"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={addForm} layout="vertical">
          <Form.Item
            name="path"
            label="path"
            rules={[
              { required: true, message: "请输入 path" },
              { pattern: /^\//, message: "path 必须以 / 开头" },
            ]}
          >
            <Input placeholder="/<line>/some-page" />
          </Form.Item>
          <Form.Item
            name="title"
            label="title"
            rules={[{ required: true, message: "请输入 title" }]}
          >
            <Input placeholder="例: 代建项目" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function AccessMatrixEditor({
  value,
  onChange,
}: {
  value: Record<string, V2DataDomain[]>;
  onChange: (next: Record<string, V2DataDomain[]>) => void;
}) {
  return (
    <Table
      rowKey="role"
      size="small"
      pagination={false}
      dataSource={V2_ACCESS_ROLES.map((r) => ({ role: r, domains: value[r] ?? [] }))}
      columns={[
        {
          title: "角色 (line-scoped)",
          dataIndex: "role",
          width: 200,
          render: (r: V2AccessRole) => (
            <Tag color={accessRoleColor(r)}>{accessRoleLabel(r)}</Tag>
          ),
        },
        {
          title: "可见数据域 (多选)",
          key: "domains",
          render: (_, row) => (
            <Checkbox.Group
              value={row.domains}
              onChange={(vals) =>
                onChange({ ...value, [row.role]: vals as V2DataDomain[] })
              }
            >
              <Space size={[12, 8]} wrap>
                {V2_DOMAINS.map((d) => (
                  <Checkbox key={d} value={d} aria-label={d}>
                    <Tag color={domainColor(d)} style={{ marginRight: 4 }}>
                      {domainLabel(d)}
                    </Tag>
                  </Checkbox>
                ))}
              </Space>
            </Checkbox.Group>
          ),
        },
      ]}
    />
  );
}

function KpiEditor({
  value,
  onChange,
}: {
  value: Record<V2KpiViewKey, DraftKpi[]>;
  onChange: (next: Record<V2KpiViewKey, DraftKpi[]>) => void;
}) {
  const [addOpen, setAddOpen] = useState(false);
  const [addView, setAddView] = useState<V2KpiViewKey>("fin_view");
  const [addForm] = Form.useForm<DraftKpi>();

  const addKpi = async () => {
    const v = await addForm.validateFields();
    onChange({
      ...value,
      [addView]: [...(value[addView] ?? []), v],
    });
    setAddOpen(false);
    addForm.resetFields();
  };

  const removeKpi = (view: V2KpiViewKey, idx: number) => {
    onChange({
      ...value,
      [view]: value[view].filter((_, i) => i !== idx),
    });
  };

  const renderKpiTable = (view: V2KpiViewKey): ReactNode => {
    const list = value[view] ?? [];
    const columns: ColumnsType<DraftKpi & { __idx: number }> = [
      {
        title: "id",
        dataIndex: "id",
        width: 200,
        render: (v: string) => <code>{v}</code>,
      },
      { title: "title", dataIndex: "title" },
      {
        title: "source",
        dataIndex: "source",
        render: (v?: string) =>
          v ? <code>{v}</code> : <Text type="secondary">—</Text>,
      },
      {
        title: "formula",
        dataIndex: "formula",
        render: (v?: string) =>
          v ? <code>{v}</code> : <Text type="secondary">—</Text>,
      },
      {
        title: "操作",
        key: "actions",
        width: 100,
        render: (_, row) => (
          <Button
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() => removeKpi(view, row.__idx)}
            aria-label="删除 KPI"
          >
            删除
          </Button>
        ),
      },
    ];
    return (
      <div data-testid={`kpi-table-${view}`}>
        <Space style={{ marginBottom: 8 }}>
          <Text strong>{kpiViewLabel(view)}</Text>
          <Tag>{list.length} 条</Tag>
        </Space>
        <Table
          rowKey={(_, idx) => `kpi-${view}-${idx ?? 0}`}
          size="small"
          pagination={false}
          dataSource={list.map((k, i) => ({ ...k, __idx: i }))}
          columns={columns}
          locale={{ emptyText: <Empty description="无 KPI" /> }}
        />
        <Button
          type="dashed"
          size="small"
          icon={<PlusOutlined />}
          onClick={() => {
            setAddView(view);
            addForm.resetFields();
            setAddOpen(true);
          }}
          style={{ marginTop: 8 }}
          aria-label={`添加 ${view} KPI`}
        >
          添加 KPI
        </Button>
      </div>
    );
  };

  return (
    <>
      <Tabs
        defaultActiveKey="fin_view"
        type="card"
        items={V2_KPI_VIEWS.map((v) => ({
          key: v,
          label: kpiViewLabel(v),
          children: renderKpiTable(v),
        }))}
      />
      <Modal
        title={`添加 KPI (${kpiViewLabel(addView)})`}
        open={addOpen}
        onCancel={() => setAddOpen(false)}
        onOk={addKpi}
        okText="添加"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={addForm} layout="vertical">
          <Form.Item
            name="id"
            label="id"
            rules={[
              { required: true, message: "请输入 id" },
              {
                pattern: /^[a-z0-9_-]+$/,
                message: "id 必须 url-safe (小写字母/数字/下划线/连字符)",
              },
            ]}
          >
            <Input placeholder="例: monthly_contract_value" />
          </Form.Item>
          <Form.Item
            name="title"
            label="title"
            rules={[{ required: true, message: "请输入 title" }]}
          >
            <Input placeholder="例: 月度代建合同额" />
          </Form.Item>
          <Form.Item name="source" label="source (可选)">
            <Input placeholder="例: mart_pm.fct_contract_value" />
          </Form.Item>
          <Form.Item name="formula" label="formula (可选)">
            <Input placeholder="派生指标表达式, 例 sum(amount) / count(*)" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
