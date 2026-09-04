// apps/web/app/(dashboard)/admin/business-lines/page.tsx
//
// Admin → 业务线清单页 (D2, 2026-09-04).
//
// 列出全部 9 条业务线 (含 v2 元数据), 每行一个 "编辑" 按钮跳到
// `/admin/business-lines/{id}` 完整编辑页.
//
// 数据流:
//   fetch /api/admin/business-lines (BFF)
//     → upstream GET {API}/api/admin/business-lines
//     → apps/api/app/routers/admin_business_lines.py:list_business_lines
//
// 模式参考 apps/web/app/(dashboard)/admin/users/page.tsx:
//   antd Card + Table + Reload 按钮 + App.useApp() message.

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  EditOutlined,
  ReloadOutlined,
  ApartmentOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import type { BusinessLineSummary, V2DataDomain } from "@biz-bp/types";

import {
  domainColor,
  domainLabel,
  listBusinessLines,
  V2_DOMAINS,
} from "../../../../lib/business-lines";

const { Title, Text } = Typography;

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

/**
 * Render the data_scope.domains as a row of coloured Tags. Manifests
 * that predate v2 come back from the server without `data_scope` —
 * in that case we surface all 5 default domains so the operator can
 * see "this line has not opted in to v2 yet" at a glance (the
 * `has_v2_fields` column also reflects that).
 */
function DomainTags({ domains }: { domains?: V2DataDomain[] }) {
  if (!domains || domains.length === 0) {
    // v1-only manifest — show all 5 as the "default" set.
    return (
      <Space size={[4, 4]} wrap>
        {V2_DOMAINS.map((d) => (
          <Tag key={d} color={domainColor(d)} style={{ opacity: 0.5 }}>
            {domainLabel(d)}
          </Tag>
        ))}
        <Text type="secondary" style={{ fontSize: 12 }}>
          (默认 — 未显式声明)
        </Text>
      </Space>
    );
  }
  return (
    <Space size={[4, 4]} wrap>
      {domains.map((d) => (
        <Tag key={d} color={domainColor(d)}>
          {domainLabel(d)}
        </Tag>
      ))}
    </Space>
  );
}

export default function AdminBusinessLinesPage() {
  const router = useRouter();
  const { message } = App.useApp();
  const [lines, setLines] = useState<BusinessLineSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listBusinessLines();
      setLines(data);
    } catch (e) {
      const msg = `加载业务线列表失败: ${getErrorMessage(e)}`;
      setError(msg);
      message.error(msg);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    load();
  }, [load]);

  // Sort by display name (zh-CN) for stable visual ordering.
  const sorted = [...lines].sort((a, b) =>
    (a.name || a.id).localeCompare(b.name || b.id, "zh-Hans-CN", {
      sensitivity: "base",
    }),
  );

  const columns: ColumnsType<BusinessLineSummary> = [
    {
      title: "ID",
      dataIndex: "id",
      width: 180,
      render: (v: string) => <code>{v}</code>,
    },
    {
      title: "名称",
      dataIndex: "name",
      width: 200,
      render: (v: string, row) => (
        <Space size="small">
          <Text strong>{v || row.id}</Text>
          {!v && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              (未填)
            </Text>
          )}
        </Space>
      ),
    },
    {
      title: "版本",
      dataIndex: "version",
      width: 90,
      render: (v: string) => <Tag color="geekblue">{v || "0.0.0"}</Tag>,
    },
    {
      title: "Owner",
      dataIndex: "owner",
      width: 220,
      render: (v: string) =>
        v ? (
          <a href={`mailto:${v}`}>{v}</a>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "数据域 (v2)",
      key: "data_scope",
      width: 340,
      render: (_, row) => <DomainTags domains={row.data_scope?.domains} />,
    },
    {
      title: "指标数",
      dataIndex: "indicators_count",
      width: 90,
      align: "right",
      sorter: (a, b) => a.indicators_count - b.indicators_count,
      render: (n: number) => (
        <Text type={n > 0 ? undefined : "secondary"}>{n}</Text>
      ),
    },
    {
      title: "v2 字段",
      dataIndex: "has_v2_fields",
      width: 100,
      render: (has: boolean) =>
        has ? (
          <Tag color="green" data-testid="has-v2-yes">
            has_v2_fields
          </Tag>
        ) : (
          <Tag color="default" data-testid="has-v2-no">
            v1 only
          </Tag>
        ),
    },
    {
      title: "操作",
      key: "actions",
      width: 110,
      fixed: "right",
      render: (_, row) => (
        <Button
          type="primary"
          size="small"
          icon={<EditOutlined />}
          onClick={() => router.push(`/admin/business-lines/${row.id}`)}
          aria-label={`编辑 ${row.id}`}
        >
          编辑
        </Button>
      ),
    },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card
        bordered={false}
        styles={{ body: { padding: 16 } }}
        title={
          <Space size="middle">
            <ApartmentOutlined aria-hidden />
            <Title level={4} style={{ margin: 0 }}>
              业务线管理
            </Title>
            <Text type="secondary">共 {lines.length} 条业务线</Text>
          </Space>
        }
        extra={
          <Button
            icon={<ReloadOutlined />}
            onClick={load}
            loading={loading}
            aria-label="重新加载"
          >
            重新加载
          </Button>
        }
      >
        {error && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message="加载失败"
            description={error}
            action={
              <Button size="small" onClick={load} aria-label="重试">
                重试
              </Button>
            }
          />
        )}
        <Table<BusinessLineSummary>
          rowKey="id"
          columns={columns}
          dataSource={sorted}
          loading={loading}
          pagination={false}
          scroll={{ x: 1200 }}
          size="small"
          locale={{
            emptyText: loading ? <Spin /> : <Empty description="暂无业务线" />,
          }}
          aria-label="业务线列表"
        />
        <div style={{ marginTop: 12, fontSize: 12, color: "#999" }}>
          提示: 点击「编辑」进入完整 manifest 编辑器 (v1 基础 / v2 数据域 / v2 角色
          分配 / v2 访问矩阵 / v2 KPI), 保存后由后端原子写回
          <code>business_lines/{`<id>`}/manifest.yaml</code>。
        </div>
      </Card>
    </div>
  );
}
