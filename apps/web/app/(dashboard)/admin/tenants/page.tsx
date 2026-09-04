// apps/web/app/(dashboard)/admin/tenants/page.tsx
//
// M3 (2026-09-04) — super-admin Tenant management page.
//
// Pattern copied from apps/web/app/(dashboard)/admin/users/page.tsx
// (the D2 admin page that manages business-line manifests). Layout:
//   * Title row: "Tenant 管理 (super admin only)" + reload + create
//   * Main: antd <Table> of all tenants with slug / name / plan /
//     is_active / created_at + actions (edit / toggle is_active).
//
// Authorization
// -------------
// The page first reads /api/auth/me-tenant to discover whether the
// current user is super admin. Non-super-admins see a "权限不足"
// page (not a redirect — we still render the layout chrome so the
// operator can navigate back).

"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Pagination,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CheckCircleOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
} from "@ant-design/icons";
import type {
  CreateTenantPayload,
  TenantInfo,
  UpdateTenantPayload,
} from "@biz-bp/types";

import {
  createTenant,
  getMyTenant,
  listTenants,
  updateTenant,
} from "../../../../lib/tenants";

const { Title, Text } = Typography;

const PAGE_SIZE = 20;

const PLAN_OPTIONS: { value: TenantInfo["plan"]; label: string; color: string }[] = [
  { value: "standard", label: "standard", color: "blue" },
  { value: "enterprise", label: "enterprise", color: "gold" },
  { value: "demo", label: "demo", color: "purple" },
];

function planColor(plan: string): string {
  return PLAN_OPTIONS.find((p) => p.value === plan)?.color ?? "default";
}

function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export default function AdminTenantsPage() {
  const { message } = App.useApp();
  const [tenants, setTenants] = useState<TenantInfo[]>([]);
  const [meIsSuper, setMeIsSuper] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);

  // modals
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<TenantInfo | null>(null);

  const [createForm] = Form.useForm<CreateTenantPayload>();
  const [editForm] = Form.useForm<UpdateTenantPayload>();

  const loadTenants = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listTenants();
      setTenants(data.tenants);
    } catch (e) {
      message.error(`加载租户列表失败: ${getErrorMessage(e)}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  const loadMe = useCallback(async () => {
    try {
      const me = await getMyTenant();
      setMeIsSuper(me?.is_super_admin ?? false);
    } catch {
      setMeIsSuper(false);
    }
  }, []);

  useEffect(() => {
    loadMe();
    loadTenants();
  }, [loadMe, loadTenants]);

  const paged = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE;
    return tenants.slice(start, start + PAGE_SIZE);
  }, [tenants, page]);

  const openEdit = (row: TenantInfo) => {
    setEditing(row);
    editForm.setFieldsValue({
      name: row.name,
      plan: row.plan,
      is_active: row.is_active,
    });
    setEditOpen(true);
  };

  const submitCreate = async () => {
    const values = await createForm.validateFields();
    try {
      const t = await createTenant(values);
      message.success(`已创建租户 ${t.name}`);
      setCreateOpen(false);
      createForm.resetFields();
      loadTenants();
    } catch (e) {
      message.error(`创建失败: ${getErrorMessage(e)}`);
    }
  };

  const submitEdit = async () => {
    if (!editing) return;
    const values = await editForm.validateFields();
    try {
      const t = await updateTenant(editing.id, values);
      message.success(`已更新 ${t.name}`);
      setEditOpen(false);
      loadTenants();
    } catch (e) {
      message.error(`更新失败: ${getErrorMessage(e)}`);
    }
  };

  const toggleActive = async (row: TenantInfo, next: boolean) => {
    try {
      await updateTenant(row.id, { is_active: next });
      message.success(`${row.name} 已${next ? "启用" : "停用"}`);
      loadTenants();
    } catch (e) {
      message.error(`切换失败: ${getErrorMessage(e)}`);
    }
  };

  const columns: ColumnsType<TenantInfo> = [
    {
      title: "ID",
      dataIndex: "id",
      width: 100,
      render: (v: string) => (
        <Tooltip title={v}>
          <code style={{ fontSize: 12 }}>{shortId(v)}</code>
        </Tooltip>
      ),
    },
    {
      title: "Slug",
      dataIndex: "slug",
      width: 180,
      render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code>,
    },
    {
      title: "名称",
      dataIndex: "name",
      width: 180,
      render: (v: string) => <strong>{v}</strong>,
    },
    {
      title: "Plan",
      dataIndex: "plan",
      width: 110,
      render: (v: string) => <Tag color={planColor(v)}>{v}</Tag>,
    },
    {
      title: "活跃",
      dataIndex: "is_active",
      width: 90,
      render: (active: boolean, row) => (
        <Switch
          checked={active}
          checkedChildren="启用"
          unCheckedChildren="停用"
          aria-label={`切换 ${row.slug} 状态`}
          onChange={(checked) => toggleActive(row, checked)}
        />
      ),
    },
    {
      title: "用户数",
      dataIndex: "user_count",
      width: 90,
      render: (n?: number) => (typeof n === "number" ? n : "—"),
    },
    {
      title: "业务线",
      dataIndex: "business_line_count",
      width: 90,
      render: (n?: number) => (typeof n === "number" ? n : "—"),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 160,
      render: (v: string) => (
        <Text style={{ fontSize: 12 }}>{v.slice(0, 19).replace("T", " ")}</Text>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      fixed: "right",
      render: (_, row) => (
        <Space size="small">
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(row)}
            aria-label={`编辑 ${row.slug}`}
          >
            编辑
          </Button>
        </Space>
      ),
    },
  ];

  // ---------------------------------------------------------------------
  // Permission gate
  // ---------------------------------------------------------------------
  if (meIsSuper === null) {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Spin tip="校验权限..." />
      </div>
    );
  }
  if (!meIsSuper) {
    return (
      <div style={{ padding: 24 }}>
        <Alert
          type="error"
          showIcon
          message="权限不足"
          description={
            <span>
              当前账号不是 super admin,无法管理租户。
              如需启用,请用 SQL:{" "}
              <code>UPDATE users SET is_super_admin = TRUE WHERE username = &apos;…&apos;;</code>
            </span>
          }
        />
      </div>
    );
  }

  return (
    <div style={{ padding: 24 }}>
      <Card
        title={
          <Space>
            <Title level={4} style={{ margin: 0 }}>
              Tenant 管理
            </Title>
            <Tag color="gold">super admin only</Tag>
          </Space>
        }
        extra={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={loadTenants}
              aria-label="重新加载"
            >
              重新加载
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                createForm.resetFields();
                createForm.setFieldsValue({
                  plan: "standard",
                  is_active: true,
                });
                setCreateOpen(true);
              }}
              aria-label="新建租户"
            >
              新建
            </Button>
          </Space>
        }
      >
        <Table<TenantInfo>
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={paged}
          pagination={false}
          size="small"
        />
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
          <Pagination
            current={page}
            total={tenants.length}
            pageSize={PAGE_SIZE}
            onChange={setPage}
            showSizeChanger={false}
            showTotal={(t) => `共 ${t} 个租户`}
          />
        </div>
      </Card>

      {/* Create modal */}
      <Modal
        title="新建租户"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={submitCreate}
        okText="创建"
        cancelText="取消"
        destroyOnClose
      >
        <Form<CreateTenantPayload> form={createForm} layout="vertical" requiredMark>
          <Form.Item
            label="Slug"
            name="slug"
            rules={[
              { required: true, message: "请输入 slug" },
              { pattern: /^[a-z0-9-]+$/, message: "slug 只能包含小写字母、数字、连字符" },
              { min: 2, max: 64, message: "slug 长度 2-64" },
            ]}
            extra="URL-safe 标识,创建后不可修改"
          >
            <Input placeholder="acme-realty" autoComplete="off" />
          </Form.Item>
          <Form.Item
            label="名称"
            name="name"
            rules={[
              { required: true, message: "请输入显示名" },
              { min: 1, max: 128, message: "名称长度 1-128" },
            ]}
          >
            <Input placeholder="Acme Realty" autoComplete="off" />
          </Form.Item>
          <Form.Item
            label="Plan"
            name="plan"
            initialValue="standard"
            rules={[{ required: true, message: "请选择 plan" }]}
          >
            <Select
              options={PLAN_OPTIONS.map((p) => ({
                value: p.value,
                label: <Tag color={p.color}>{p.label}</Tag>,
              }))}
            />
          </Form.Item>
          <Form.Item
            label="启用"
            name="is_active"
            valuePropName="checked"
            initialValue={true}
          >
            <Switch checkedChildren="启用" unCheckedChildren="停用" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edit modal — slug intentionally NOT editable */}
      <Modal
        title={`编辑 ${editing?.name ?? ""}`}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={submitEdit}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form<UpdateTenantPayload> form={editForm} layout="vertical" requiredMark>
          <Form.Item label="Slug (不可修改)">
            <Input value={editing?.slug} disabled />
          </Form.Item>
          <Form.Item
            label="名称"
            name="name"
            rules={[
              { required: true, message: "请输入显示名" },
              { min: 1, max: 128, message: "名称长度 1-128" },
            ]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item
            label="Plan"
            name="plan"
            rules={[{ required: true, message: "请选择 plan" }]}
          >
            <Select
              options={PLAN_OPTIONS.map((p) => ({
                value: p.value,
                label: <Tag color={p.color}>{p.label}</Tag>,
              }))}
            />
          </Form.Item>
          <Form.Item
            label="启用"
            name="is_active"
            valuePropName="checked"
          >
            <Switch checkedChildren="启用" unCheckedChildren="停用" />
          </Form.Item>
        </Form>
      </Modal>

      <div style={{ marginTop: 16, color: "rgba(0,0,0,0.45)", fontSize: 12 }}>
        <Space>
          <CheckCircleOutlined />
          <span>tenant row 创建后 slug 不可修改 (URL 安全标识)</span>
          <StopOutlined style={{ marginLeft: 12 }} />
          <span>停用后该 tenant 的用户依然能登录,但所有 RLS 查询会回落到默认租户</span>
        </Space>
      </div>
    </div>
  );
}
