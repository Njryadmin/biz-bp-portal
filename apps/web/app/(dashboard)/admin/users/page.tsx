// apps/web/app/(dashboard)/admin/users/page.tsx
//
// Admin → User management.
//
// Renders a table of all users (admin only) with inline editing of
// display_name / email / is_active, plus pop-up modals for:
//   * Create new user
//   * Edit roles / accessible_lines
//   * Reset password
//   * Confirm delete
//
// All business-line data is fetched from the registry so the line
// picker is data-driven (no hard-coded list).

"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  Modal,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message as antdMessage,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  DeleteOutlined,
  EditOutlined,
  KeyOutlined,
  PlusOutlined,
  ReloadOutlined,
  UserAddOutlined,
} from "@ant-design/icons";

import {
  createUser,
  deactivateUser,
  listUsers,
  resetUserPassword,
  updateUser,
  updateUserLines,
  updateUserRoles,
  type AdminUserItem,
  type CreateUserPayload,
  type UpdateUserPayload,
} from "../../../../lib/auth";

const { Title, Text } = Typography;

// Hard-coded role constants. We could move these to a config endpoint
// but they're stable (admin / viewer / auditor / bp:<line> are part of
// the RBAC spec, not user-editable).
const FIXED_ROLES: { value: string; label: string; color: string }[] = [
  { value: "admin", label: "admin", color: "red" },
  { value: "viewer", label: "viewer", color: "blue" },
  { value: "auditor", label: "auditor", color: "purple" },
];

const PAGE_SIZE = 20;

interface LineOption {
  id: string;
  display_name: string;
  name?: string;
}

interface LinesResponse {
  version?: string;
  lines: LineOption[];
}

async function fetchLines(): Promise<LineOption[]> {
  try {
    const res = await fetch("/api/registry", { cache: "no-store" });
    if (!res.ok) return [];
    const data = (await res.json()) as LinesResponse;
    return data.lines ?? [];
  } catch {
    return [];
  }
}

function roleColor(role: string): string {
  if (role === "admin") return "red";
  if (role === "auditor") return "purple";
  if (role === "viewer") return "blue";
  if (role.startsWith("bp:")) return "green";
  return "default";
}

function lineTagColor(lineId: string): string {
  // Stable, well-spread hash → color so the UI looks consistent
  // across reloads (no flicker on re-render).
  let h = 0;
  for (let i = 0; i < lineId.length; i++) h = (h * 31 + lineId.charCodeAt(i)) >>> 0;
  const palette = ["blue", "geekblue", "cyan", "green", "lime", "gold", "orange", "magenta", "volcano"];
  return palette[h % palette.length];
}

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export default function AdminUsersPage() {
  const { message } = App.useApp();
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [lines, setLines] = useState<LineOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  // Modals
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<AdminUserItem | null>(null);
  const [pwOpen, setPwOpen] = useState(false);
  const [pwTarget, setPwTarget] = useState<AdminUserItem | null>(null);

  const [createForm] = Form.useForm<CreateUserPayload>();
  const [editForm] = Form.useForm<UpdateUserPayload & { roles: string[]; accessible_lines: string[] }>();
  const [pwForm] = Form.useForm<{ new_password: string; confirm: string; reveal: boolean }>();

  // ---------------------------------------------------------------------
  // Loaders
  // ---------------------------------------------------------------------
  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listUsers();
      setUsers(data.users);
    } catch (e) {
      message.error(`加载用户列表失败: ${getErrorMessage(e)}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  const loadLines = useCallback(async () => {
    const data = await fetchLines();
    setLines(data);
  }, []);

  useEffect(() => {
    loadUsers();
    loadLines();
  }, [loadUsers, loadLines]);

  // ---------------------------------------------------------------------
  // Derived data
  // ---------------------------------------------------------------------
  const filtered = useMemo(() => {
    if (!search.trim()) return users;
    const q = search.toLowerCase();
    return users.filter(
      (u) =>
        u.username.toLowerCase().includes(q) ||
        (u.display_name ?? "").toLowerCase().includes(q) ||
        (u.email ?? "").toLowerCase().includes(q) ||
        u.roles.some((r) => r.toLowerCase().includes(q)),
    );
  }, [users, search]);

  const paged = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, page]);

  const lineOptions = useMemo(
    () =>
      lines.map((l) => ({
        value: l.id,
        label: l.display_name ?? l.name ?? l.id,
      })),
    [lines],
  );

  const roleOptions = useMemo(() => {
    // Fixed roles + one "bp:<line>" per registered line
    const opts: { value: string; label: string }[] = [];
    FIXED_ROLES.forEach((r) => opts.push({ value: r.value, label: r.label }));
    lines.forEach((l) => opts.push({ value: `bp:${l.id}`, label: `bp:${l.id}` }));
    return opts;
  }, [lines]);

  // ---------------------------------------------------------------------
  // Column defs
  // ---------------------------------------------------------------------
  const columns: ColumnsType<AdminUserItem> = [
    {
      title: "ID",
      dataIndex: "id",
      width: 60,
      sorter: (a, b) => a.id - b.id,
    },
    {
      title: "用户名",
      dataIndex: "username",
      width: 160,
      render: (v: string) => <code>{v}</code>,
    },
    {
      title: "显示名",
      dataIndex: "display_name",
      width: 160,
    },
    {
      title: "邮箱",
      dataIndex: "email",
      width: 200,
      render: (v: string | null) =>
        v ? <a href={`mailto:${v}`}>{v}</a> : <Text type="secondary">—</Text>,
    },
    {
      title: "角色",
      dataIndex: "roles",
      width: 240,
      render: (roles: string[]) =>
        roles.length === 0 ? (
          <Text type="secondary">—</Text>
        ) : (
          <Space size={[4, 4]} wrap>
            {roles.map((r) => (
              <Tag key={r} color={roleColor(r)}>
                {r}
              </Tag>
            ))}
          </Space>
        ),
    },
    {
      title: "可见业务线",
      dataIndex: "accessible_lines",
      width: 260,
      render: (ids: string[]) =>
        ids.length === 0 ? (
          <Text type="secondary">—</Text>
        ) : (
          <Space size={[4, 4]} wrap>
            {ids.map((id) => (
              <Tag key={id} color={lineTagColor(id)}>
                {id}
              </Tag>
            ))}
          </Space>
        ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 100,
      render: (active: boolean, row) => (
        <Switch
          checked={active}
          checkedChildren="启用"
          unCheckedChildren="停用"
          aria-label={`切换 ${row.username} 状态`}
          onChange={async (checked) => {
            try {
              await updateUser(row.id, { is_active: checked });
              message.success(`${row.username} 已${checked ? "启用" : "停用"}`);
              loadUsers();
            } catch (e) {
              message.error(`切换失败: ${getErrorMessage(e)}`);
            }
          }}
        />
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 280,
      fixed: "right",
      render: (_, row) => (
        <Space size="small" wrap>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(row)}
            aria-label={`编辑 ${row.username}`}
          >
            编辑
          </Button>
          <Button
            size="small"
            icon={<KeyOutlined />}
            onClick={() => openResetPassword(row)}
            aria-label={`重置 ${row.username} 密码`}
          >
            重置密码
          </Button>
          <Popconfirm
            title={`确认停用 ${row.username}?`}
            description="停用后该用户无法登录(保留记录以便审计)。"
            okText="停用"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={async () => {
              try {
                await deactivateUser(row.id);
                message.success(`已停用 ${row.username}`);
                loadUsers();
              } catch (e) {
                message.error(`停用失败: ${getErrorMessage(e)}`);
              }
            }}
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              aria-label={`停用 ${row.username}`}
            >
              停用
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ---------------------------------------------------------------------
  // Modal handlers
  // ---------------------------------------------------------------------
  const openEdit = (row: AdminUserItem) => {
    setEditing(row);
    editForm.setFieldsValue({
      display_name: row.display_name,
      email: row.email ?? "",
      is_active: row.is_active,
      roles: row.roles,
      accessible_lines: row.accessible_lines,
    });
    setEditOpen(true);
  };

  const openResetPassword = (row: AdminUserItem) => {
    setPwTarget(row);
    pwForm.resetFields();
    setPwOpen(true);
  };

  const submitCreate = async () => {
    const values = await createForm.validateFields();
    try {
      await createUser(values);
      message.success(`已创建 ${values.username}`);
      setCreateOpen(false);
      createForm.resetFields();
      loadUsers();
    } catch (e) {
      message.error(`创建失败: ${getErrorMessage(e)}`);
    }
  };

  const submitEdit = async () => {
    if (!editing) return;
    const values = await editForm.validateFields();
    const { display_name, email, is_active, roles, accessible_lines } = values;
    try {
      // Split the form into three PATCHes so each endpoint owns its
      // own field set. The order doesn't matter — they're idempotent
      // and the last write wins.
      await updateUser(editing.id, {
        display_name,
        email: email || undefined,
        is_active,
      } as UpdateUserPayload);
      // roles + lines: use the unified /roles endpoint so a single
      // transaction recomputes the bp:<line> ↔ accessible_lines union.
      await updateUserRoles(editing.id, roles, accessible_lines);
      message.success(`已保存 ${editing.username}`);
      setEditOpen(false);
      setEditing(null);
      loadUsers();
    } catch (e) {
      message.error(`保存失败: ${getErrorMessage(e)}`);
    }
  };

  const submitResetPassword = async () => {
    if (!pwTarget) return;
    const values = await pwForm.validateFields();
    if (values.new_password !== values.confirm) {
      message.error("两次输入的密码不一致");
      return;
    }
    try {
      const resp = await resetUserPassword(pwTarget.id, {
        new_password: values.new_password,
        reveal: values.reveal,
      });
      if (resp.new_password) {
        Modal.info({
          title: "新密码已生成",
          content: (
            <div>
              <p>请通过安全渠道(线下/1Password)把以下密码告知 {pwTarget.username}：</p>
              <Input.TextArea
                readOnly
                rows={2}
                defaultValue={resp.new_password}
                aria-label="新密码"
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                关闭此弹窗后密码不可再查看。
              </Text>
            </div>
          ),
        });
      } else {
        message.success(`已重置 ${pwTarget.username} 的密码`);
      }
      setPwOpen(false);
      setPwTarget(null);
    } catch (e) {
      message.error(`重置失败: ${getErrorMessage(e)}`);
    }
  };

  // ---------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card
        bordered={false}
        styles={{ body: { padding: 16 } }}
        title={
          <Space size="middle">
            <Title level={4} style={{ margin: 0 }}>
              用户管理
            </Title>
            <Text type="secondary">共 {users.length} 个用户</Text>
          </Space>
        }
        extra={
          <Space>
            <Input.Search
              placeholder="搜索 用户名 / 邮箱 / 角色"
              allowClear
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              style={{ width: 240 }}
              aria-label="搜索用户"
            />
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                loadUsers();
                loadLines();
              }}
              aria-label="刷新"
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setCreateOpen(true)}
              aria-label="新增用户"
            >
              新增用户
            </Button>
          </Space>
        }
      >
        <Table<AdminUserItem>
          rowKey="id"
          columns={columns}
          dataSource={paged}
          loading={loading}
          pagination={false}
          scroll={{ x: 1280 }}
          size="small"
          aria-label="用户列表"
        />
        <div
          style={{
            marginTop: 12,
            display: "flex",
            justifyContent: "flex-end",
          }}
        >
          <Pagination
            current={page}
            total={filtered.length}
            pageSize={PAGE_SIZE}
            showSizeChanger={false}
            onChange={setPage}
            aria-label="分页"
          />
        </div>
      </Card>

      {/* --------------------------------------------------------------- */}
      {/* Create modal                                                   */}
      {/* --------------------------------------------------------------- */}
      <Modal
        title={
          <Space>
            <UserAddOutlined />
            新增用户
          </Space>
        }
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={submitCreate}
        okText="创建"
        cancelText="取消"
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          initialValues={{ roles: [], accessible_lines: [] }}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[
              { required: true, message: "请输入用户名" },
              {
                pattern: /^[a-zA-Z0-9_-]+$/,
                message: "仅允许字母/数字/下划线/连字符",
              },
              { min: 2, max: 64, message: "长度 2-64" },
            ]}
          >
            <Input placeholder="例: bp-retail" autoFocus aria-label="用户名" />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始密码"
            rules={[
              { required: true, message: "请输入初始密码" },
              { min: 6, max: 256, message: "长度 6-256" },
            ]}
          >
            <Input.Password placeholder="至少 6 位" aria-label="初始密码" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input placeholder="可选,默认与用户名一致" aria-label="显示名" />
          </Form.Item>
          <Form.Item
            name="email"
            label="邮箱"
            rules={[{ type: "email", message: "邮箱格式不正确" }]}
          >
            <Input placeholder="可选" aria-label="邮箱" />
          </Form.Item>
          <Form.Item name="roles" label="角色">
            <Select
              mode="multiple"
              allowClear
              placeholder="选择角色(可多选)"
              options={roleOptions}
              aria-label="角色"
            />
          </Form.Item>
          <Form.Item name="accessible_lines" label="可见业务线">
            <Select
              mode="multiple"
              allowClear
              placeholder="选择该用户可见的业务线"
              options={lineOptions}
              aria-label="可见业务线"
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* --------------------------------------------------------------- */}
      {/* Edit modal                                                     */}
      {/* --------------------------------------------------------------- */}
      <Modal
        title={
          editing ? (
            <Space>
              <EditOutlined />
              编辑用户 — <code>{editing.username}</code>
            </Space>
          ) : null
        }
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={submitEdit}
        okText="保存"
        cancelText="取消"
        width={640}
        destroyOnClose
      >
        <Form form={editForm} layout="vertical">
          <Form.Item label="用户名 (不可改)">
            <Input value={editing?.username ?? ""} disabled aria-label="用户名" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input placeholder="显示名" aria-label="显示名" />
          </Form.Item>
          <Form.Item
            name="email"
            label="邮箱"
            rules={[{ type: "email", message: "邮箱格式不正确" }]}
          >
            <Input placeholder="可选" aria-label="邮箱" />
          </Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="停用" aria-label="启用" />
          </Form.Item>
          <Form.Item
            name="roles"
            label="角色"
            tooltip="bp:<line> 角色会自动给该用户分配对应业务线访问权限"
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="选择角色"
              options={roleOptions}
              aria-label="角色"
            />
          </Form.Item>
          <Form.Item
            name="accessible_lines"
            label="可见业务线"
            tooltip="由 bp:<line> 角色隐含的业务线会自动包含在此列表中"
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="选择业务线"
              options={lineOptions}
              aria-label="可见业务线"
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* --------------------------------------------------------------- */}
      {/* Reset password modal                                           */}
      {/* --------------------------------------------------------------- */}
      <Modal
        title={
          pwTarget ? (
            <Space>
              <KeyOutlined />
              重置密码 — <code>{pwTarget.username}</code>
            </Space>
          ) : null
        }
        open={pwOpen}
        onCancel={() => setPwOpen(false)}
        onOk={submitResetPassword}
        okText="重置"
        cancelText="取消"
        destroyOnClose
      >
        <Form
          form={pwForm}
          layout="vertical"
          initialValues={{ reveal: false }}
        >
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 6, max: 256, message: "长度 6-256" },
            ]}
          >
            <Input.Password placeholder="至少 6 位" autoFocus aria-label="新密码" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("new_password") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error("两次输入的密码不一致"));
                },
              }),
            ]}
          >
            <Input.Password placeholder="再输入一次" aria-label="确认新密码" />
          </Form.Item>
          <Form.Item name="reveal" valuePropName="checked">
            <Checkbox>
              <Space>
                重置后在弹窗中显示明文密码
                <Text type="secondary" style={{ fontSize: 12 }}>
                  (用于把新密码告知该用户;关闭后不可再查看)
                </Text>
              </Space>
            </Checkbox>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
