// apps/web/app/(dashboard)/admin/ai-models/page.tsx
//
// Admin → AI Model registry (runtime-toggleable LLM provider).
//
// Renders a table of all registered models (admin only) with inline
// actions: 测试 / 编辑 / 设为默认 / 停用. A "新建模型" button opens a
// create modal; the same form is reused for edit.
//
// Mirrors the user-mgmt UX: Ant Design Table + Modal + Form + Tag +
// Tooltip. No fancy extras.

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  EditOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  StarFilled,
  StarOutlined,
  StopOutlined,
} from "@ant-design/icons";

import {
  AI_PROVIDER_OPTIONS,
  createAIModel,
  deleteAIModel,
  listAIModels,
  providerColor,
  providerLabel,
  setDefaultAIModel,
  testAIModel,
  updateAIModel,
  type AIModelItem,
  type CreateAIModelPayload,
  type TestAIModelResponse,
  type UpdateAIModelPayload,
} from "../../../../lib/ai-models";

const { Title, Text, Paragraph } = Typography;

interface EditFormValues {
  name: string;
  provider: string;
  model_name: string;
  base_url?: string;
  api_key?: string;
  enabled: boolean;
  is_default: boolean;
}

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

function statusColor(status: string | null | undefined): string {
  if (!status) return "default";
  if (status === "ok") return "green";
  if (status === "error") return "red";
  return "blue";
}

function statusIcon(status: string | null | undefined) {
  if (status === "ok") return <CheckCircleOutlined />;
  if (status === "error") return <CloseCircleOutlined />;
  return null;
}

function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return "—";
  // Show only the time-of-day portion when the date is today, else
  // include the date. Keeps the table scannable.
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString("zh-CN", { hour12: false });
}

export default function AdminAIModelsPage() {
  const { message } = App.useApp();
  const [models, setModels] = useState<AIModelItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<AIModelItem | null>(null);
  const [testResult, setTestResult] = useState<{
    model: AIModelItem;
    result: TestAIModelResponse;
  } | null>(null);
  const [testingId, setTestingId] = useState<number | null>(null);
  // When true, the next save of the edit form will explicitly clear
  // the stored API key (the input field is disabled in this state
  // and the submit handler sends api_key=""). The backend router
  // already interprets an explicit empty string as "set api_key to
  // NULL" (apps/api/app/routers/ai_models.py:317-319).
  const [clearApiKey, setClearApiKey] = useState(false);

  const [createForm] = Form.useForm<CreateAIModelPayload>();
  const [editForm] = Form.useForm<EditFormValues>();

  // -----------------------------------------------------------------
  // Loaders
  // -----------------------------------------------------------------
  const loadModels = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listAIModels();
      setModels(data.models);
    } catch (e) {
      message.error(`加载 AI 模型列表失败: ${getErrorMessage(e)}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    loadModels();
  }, [loadModels]);

  // -----------------------------------------------------------------
  // Derived: counts
  // -----------------------------------------------------------------
  const summary = useMemo(() => {
    const active = models.filter((m) => m.is_active && m.enabled);
    const defaultModel = models.find((m) => m.is_default);
    return {
      total: models.length,
      active: active.length,
      defaultName: defaultModel?.name ?? "—",
    };
  }, [models]);

  // -----------------------------------------------------------------
  // Modal handlers
  // -----------------------------------------------------------------
  const openEdit = (row: AIModelItem) => {
    setEditing(row);
    editForm.setFieldsValue({
      name: row.name,
      provider: row.provider,
      model_name: row.model_name,
      base_url: row.base_url ?? "",
      api_key: "",
      enabled: row.enabled,
      is_default: row.is_default,
    });
    // Always start with clearApiKey=false on a fresh edit modal so
    // the previous "clear pending" toggle doesn't leak across rows.
    setClearApiKey(false);
    setEditOpen(true);
  };

  const submitCreate = async () => {
    const values = await createForm.validateFields();
    try {
      const created = await createAIModel({
        ...values,
        base_url: values.base_url || undefined,
        api_key: values.api_key || undefined,
        enabled: values.enabled ?? true,
        is_default: values.is_default ?? false,
      });
      message.success(`已创建 ${created.name}`);
      setCreateOpen(false);
      createForm.resetFields();
      loadModels();
    } catch (e) {
      message.error(`创建失败: ${getErrorMessage(e)}`);
    }
  };

  const submitEdit = async () => {
    if (!editing) return;
    const values = await editForm.validateFields();
    try {
      const payload: UpdateAIModelPayload = {
        name: values.name,
        provider: values.provider as UpdateAIModelPayload["provider"],
        model_name: values.model_name,
        enabled: values.enabled,
      };
      if (values.base_url !== undefined) {
        payload.base_url = values.base_url;
      }
      // api_key handling:
      //  - clearApiKey=true  → send api_key=""  (backend writes NULL)
      //  - clearApiKey=false AND user typed something → send the new value
      //  - clearApiKey=false AND input empty → omit the field (keep current)
      if (clearApiKey) {
        payload.api_key = "";
      } else if (values.api_key && values.api_key.length > 0) {
        payload.api_key = values.api_key;
      }
      if (values.is_default !== editing.is_default) {
        payload.is_default = values.is_default;
      }
      await updateAIModel(editing.id, payload);
      message.success(`已保存 ${values.name}`);
      setEditOpen(false);
      setEditing(null);
      loadModels();
    } catch (e) {
      message.error(`保存失败: ${getErrorMessage(e)}`);
    }
  };

  const onTest = async (row: AIModelItem) => {
    setTestingId(row.id);
    try {
      const result = await testAIModel(row.id, { prompt: "ping", max_tokens: 16 });
      setTestResult({ model: row, result });
      if (result.ok) {
        message.success(`${row.name} 测试通过 (${result.latency_ms}ms)`);
      } else {
        message.error(`${row.name} 测试失败: ${result.error || "未知错误"}`);
      }
      loadModels();
    } catch (e) {
      message.error(`测试请求失败: ${getErrorMessage(e)}`);
    } finally {
      setTestingId(null);
    }
  };

  const onSetDefault = async (row: AIModelItem) => {
    try {
      await setDefaultAIModel(row.id);
      message.success(`已将 ${row.name} 设为默认`);
      loadModels();
    } catch (e) {
      message.error(`设为默认失败: ${getErrorMessage(e)}`);
    }
  };

  const onDelete = async (row: AIModelItem) => {
    try {
      await deleteAIModel(row.id);
      message.success(`已停用 ${row.name}`);
      loadModels();
    } catch (e) {
      message.error(`停用失败: ${getErrorMessage(e)}`);
    }
  };

  const onReactivate = async (row: AIModelItem) => {
    // PATCH /api/ai-models/{id} with is_active=true. The PATCH route
    // already accepts is_active; we just reuse the existing
    // updateAIModel client helper. The backend's "promote a mock
    // row to default if no default exists" safety net also runs on
    // every API startup, so a re-activated model that becomes the
    // only active row will start as a regular (non-default) row.
    try {
      await updateAIModel(row.id, { is_active: true });
      message.success(`已启用 ${row.name}`);
      loadModels();
    } catch (e) {
      message.error(`启用失败: ${getErrorMessage(e)}`);
    }
  };

  // -----------------------------------------------------------------
  // Column defs
  // -----------------------------------------------------------------
  const columns: ColumnsType<AIModelItem> = [
    {
      title: "名称",
      dataIndex: "name",
      width: 200,
      render: (v: string, row) => (
        <Space size={4} direction="vertical" style={{ lineHeight: 1.2 }}>
          <Space size={4}>
            <Text strong>{v}</Text>
            {row.is_default ? (
              <Tooltip title="当前默认模型">
                <StarFilled style={{ color: "#faad14" }} />
              </Tooltip>
            ) : null}
          </Space>
          <Text type="secondary" style={{ fontSize: 11 }}>
            id={row.id} · {row.is_active ? "active" : "inactive"}
          </Text>
        </Space>
      ),
    },
    {
      title: "Provider",
      dataIndex: "provider",
      width: 140,
      render: (v: string) => <Tag color={providerColor(v)}>{providerLabel(v)}</Tag>,
    },
    {
      title: "Model",
      dataIndex: "model_name",
      width: 200,
      render: (v: string, row) => (
        <Space size={4} direction="vertical" style={{ lineHeight: 1.2 }}>
          <code style={{ fontSize: 12 }}>{v}</code>
          {row.base_url ? (
            <Text type="secondary" style={{ fontSize: 11 }} copyable={false}>
              {row.base_url}
            </Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "enabled",
      width: 110,
      render: (enabled: boolean, row) =>
        row.is_active ? (
          <Tag color={enabled ? "green" : "default"}>
            {enabled ? "已启用" : "已停用"}
          </Tag>
        ) : (
          <Tag color="red">已删除</Tag>
        ),
    },
    {
      title: "API Key",
      dataIndex: "api_key_set",
      width: 110,
      render: (set: boolean, row) =>
        set ? (
          row.api_key_is_env_ref ? (
            <Tooltip title="通过 env: 引用,运行时从环境变量读取">
              <Tag color="blue">env ref</Tag>
            </Tooltip>
          ) : (
            <Tooltip title="已存储 (加密)">
              <Tag color="green">已设置</Tag>
            </Tooltip>
          )
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "默认",
      dataIndex: "is_default",
      width: 80,
      render: (isDefault: boolean, row) =>
        isDefault ? (
          <Tag icon={<StarFilled />} color="gold">
            默认
          </Tag>
        ) : row.is_active && row.enabled ? (
          <Button
            size="small"
            type="link"
            icon={<StarOutlined />}
            onClick={() => onSetDefault(row)}
            aria-label={`将 ${row.name} 设为默认`}
          >
            设为默认
          </Button>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "最近测试",
      key: "last_test",
      width: 200,
      render: (_, row) => (
        <Space size={4} direction="vertical" style={{ lineHeight: 1.2 }}>
          {row.last_test_status ? (
            <Tooltip
              title={
                row.last_test_response
                  ? `最近响应: ${row.last_test_response.slice(0, 300)}`
                  : null
              }
            >
              <Tag color={statusColor(row.last_test_status)} icon={statusIcon(row.last_test_status)}>
                {row.last_test_status} · {formatLatency(row.last_test_latency_ms)}
              </Tag>
            </Tooltip>
          ) : (
            <Text type="secondary">未测试</Text>
          )}
          {row.last_tested_at ? (
            <Text type="secondary" style={{ fontSize: 11 }}>
              {formatTimestamp(row.last_tested_at)}
            </Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 280,
      fixed: "right",
      render: (_, row) => (
        <Space size="small" wrap>
          <Tooltip title="向此模型发送一次 'ping' 调用,记录结果">
            <Button
              size="small"
              icon={<PlayCircleOutlined />}
              loading={testingId === row.id}
              onClick={() => onTest(row)}
              aria-label={`测试 ${row.name}`}
            >
              测试
            </Button>
          </Tooltip>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(row)}
            aria-label={`编辑 ${row.name}`}
          >
            编辑
          </Button>
          <Popconfirm
            title={`确认停用 ${row.name}?`}
            description="停用后此模型不会被引擎使用(记录保留)。"
            okText="停用"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            disabled={!row.is_active}
            onConfirm={() => onDelete(row)}
          >
            <Button
              size="small"
              danger
              icon={<StopOutlined />}
              disabled={!row.is_active}
              aria-label={`停用 ${row.name}`}
            >
              停用
            </Button>
          </Popconfirm>
          {/*
            Re-enable button: shown only for soft-deleted rows
            (is_active=false). The previous design only had a
            "停用" button that became disabled for inactive rows,
            leaving no way to re-enable from the table — the
            operator had to open the edit modal and flip the
            "启用" switch by hand, which was non-obvious.
          */}
          {!row.is_active ? (
            <Popconfirm
              title={`确认启用 ${row.name}?`}
              description="启用后此模型会重新出现在引擎候选列表中(不会自动成为默认)。"
              okText="启用"
              cancelText="取消"
              onConfirm={() => onReactivate(row)}
            >
              <Button
                size="small"
                type="primary"
                icon={<CheckCircleOutlined />}
                aria-label={`启用 ${row.name}`}
              >
                启用
              </Button>
            </Popconfirm>
          ) : null}
        </Space>
      ),
    },
  ];

  // -----------------------------------------------------------------
  // Empty-state
  // -----------------------------------------------------------------
  const isEmpty = !loading && models.length === 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card
        bordered={false}
        styles={{ body: { padding: 16 } }}
        title={
          <Space size="middle">
            <Title level={4} style={{ margin: 0 }}>
              AI 模型注册表
            </Title>
            <Text type="secondary">
              共 {summary.total} 个 · 已启用 {summary.active} · 默认 {summary.defaultName}
            </Text>
          </Space>
        }
        extra={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={loadModels}
              aria-label="刷新"
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setCreateOpen(true)}
              aria-label="新建模型"
            >
              新建模型
            </Button>
          </Space>
        }
      >
        {isEmpty ? (
          <Alert
            type="info"
            showIcon
            message="暂未配置 AI 模型,使用内置 mock"
            description={
              <Space direction="vertical" size={4} style={{ marginTop: 4 }}>
                <Paragraph style={{ marginBottom: 0 }}>
                  点击「新建」添加 DeepSeek / OpenAI / Ollama / 自定义 OpenAI 兼容端点。
                </Paragraph>
                <Button
                  type="primary"
                  icon={<PlusOutlined />}
                  onClick={() => setCreateOpen(true)}
                >
                  新建
                </Button>
              </Space>
            }
          />
        ) : (
          <Table<AIModelItem>
            rowKey="id"
            columns={columns}
            dataSource={models}
            loading={loading}
            pagination={false}
            scroll={{ x: 1280 }}
            size="small"
            aria-label="AI 模型列表"
            rowClassName={(row) => (row.is_default ? "ai-model-row-default" : "")}
          />
        )}
      </Card>

      {/* ------------------------------------------------------------- */}
      {/* Create modal                                                  */}
      {/* ------------------------------------------------------------- */}
      <Modal
        title="新建 AI 模型"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={submitCreate}
        okText="创建"
        cancelText="取消"
        destroyOnClose
        width={640}
      >
        <Form
          form={createForm}
          layout="vertical"
          initialValues={{ provider: "deepseek", enabled: true, is_default: false }}
        >
          <Form.Item
            name="name"
            label="名称"
            rules={[
              { required: true, message: "请输入名称" },
              { min: 1, max: 64, message: "长度 1-64" },
            ]}
          >
            <Input placeholder="例: DeepSeek-V3-Prod" autoFocus aria-label="名称" />
          </Form.Item>
          <Form.Item
            name="provider"
            label="Provider"
            rules={[{ required: true, message: "请选择 Provider" }]}
          >
            <Select
              options={AI_PROVIDER_OPTIONS}
              placeholder="选择 LLM provider"
              aria-label="Provider"
            />
          </Form.Item>
          <Form.Item
            name="model_name"
            label="模型名"
            rules={[
              { required: true, message: "请输入模型名" },
              { min: 1, max: 128, message: "长度 1-128" },
            ]}
          >
            <Input placeholder="例: deepseek-chat / gpt-4o-mini / qwen2.5:7b" aria-label="模型名" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL (可选)">
            <Input
              placeholder="Ollama: http://host:11434  |  OpenAI: https://api.openai.com/v1/chat/completions"
              aria-label="Base URL"
            />
          </Form.Item>
          <Form.Item
            name="api_key"
            label="API Key (可选,支持 env:VAR_NAME 引用)"
            tooltip="如不想把 key 写入数据库,填写 env:DEEPSEEK_API_KEY 这样的引用,运行时从环境变量读取"
          >
            <Input.Password
              placeholder={
                clearApiKey
                  ? "将清空已存的 API Key"
                  : editing?.api_key_set
                    ? "留空保持不变 · 输入新值覆盖 · 点清空按钮清除"
                    : "sk-... 或 env:VAR_NAME"
              }
              disabled={clearApiKey}
              aria-label="API Key"
              addonAfter={
                clearApiKey ? (
                  <Button
                    type="link"
                    size="small"
                    onClick={() => {
                      setClearApiKey(false);
                      editForm.setFieldsValue({ api_key: "" });
                    }}
                    aria-label="取消清空"
                  >
                    取消
                  </Button>
                ) : (
                  <Button
                    type="link"
                    size="small"
                    onClick={() => {
                      setClearApiKey(true);
                      editForm.setFieldsValue({ api_key: "" });
                    }}
                    aria-label="清空 API Key"
                    disabled={!editing?.api_key_set}
                  >
                    清空
                  </Button>
                )
              }
            />
          </Form.Item>
          <Space size="large">
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="停用" aria-label="启用" />
            </Form.Item>
            <Form.Item name="is_default" label="设为默认" valuePropName="checked">
              <Switch checkedChildren="默认" unCheckedChildren="非默认" aria-label="设为默认" />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      {/* ------------------------------------------------------------- */}
      {/* Edit modal                                                    */}
      {/* ------------------------------------------------------------- */}
      <Modal
        title={editing ? `编辑模型 — ${editing.name}` : "编辑模型"}
        open={editOpen}
        onCancel={() => {
          setEditOpen(false);
          setClearApiKey(false);
        }}
        onOk={submitEdit}
        okText="保存"
        cancelText="取消"
        destroyOnClose
        width={640}
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[
              { required: true, message: "请输入名称" },
              { min: 1, max: 64, message: "长度 1-64" },
            ]}
          >
            <Input aria-label="名称" />
          </Form.Item>
          <Form.Item
            name="provider"
            label="Provider"
            rules={[{ required: true, message: "请选择 Provider" }]}
          >
            <Select options={AI_PROVIDER_OPTIONS} aria-label="Provider" />
          </Form.Item>
          <Form.Item
            name="model_name"
            label="模型名"
            rules={[
              { required: true, message: "请输入模型名" },
              { min: 1, max: 128, message: "长度 1-128" },
            ]}
          >
            <Input aria-label="模型名" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL">
            <Input aria-label="Base URL" />
          </Form.Item>
          <Form.Item
            name="api_key"
            label="API Key (留空表示保持现有值;输入新值覆盖)"
            tooltip={
              editing?.api_key_set
                ? editing.api_key_is_env_ref
                  ? "当前为 env 引用。填入新值将覆盖。"
                  : "当前已存储。填入新值将覆盖。"
                : "尚未设置。"
            }
          >
            <Input.Password placeholder="sk-... 或 env:VAR_NAME (留空保持)" aria-label="API Key" />
          </Form.Item>
          <Space size="large">
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="停用" aria-label="启用" />
            </Form.Item>
            <Form.Item name="is_default" label="默认" valuePropName="checked">
              <Switch checkedChildren="默认" unCheckedChildren="非默认" aria-label="默认" />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      {/* ------------------------------------------------------------- */}
      {/* Test result modal                                             */}
      {/* ------------------------------------------------------------- */}
      <Modal
        title={testResult ? `测试结果 — ${testResult.model.name}` : "测试结果"}
        open={!!testResult}
        onCancel={() => setTestResult(null)}
        footer={[
          <Button key="ok" type="primary" onClick={() => setTestResult(null)}>
            关闭
          </Button>,
        ]}
        width={640}
      >
        {testResult ? (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Space size="middle">
              <Tag
                color={statusColor(testResult.result.status)}
                icon={statusIcon(testResult.result.status)}
              >
                {testResult.result.ok ? "成功" : "失败"} ·{" "}
                {formatLatency(testResult.result.latency_ms)}
              </Tag>
              <Text type="secondary">provider: {testResult.model.provider}</Text>
              <Text type="secondary">model: {testResult.model.model_name}</Text>
            </Space>
            {testResult.result.error ? (
              <Alert
                type="error"
                showIcon
                message="错误信息"
                description={
                  <pre
                    style={{
                      margin: 0,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontSize: 12,
                    }}
                  >
                    {testResult.result.error}
                  </pre>
                }
              />
            ) : null}
            {testResult.result.sample_response ? (
              <Alert
                type="success"
                showIcon
                message="模型响应片段"
                description={
                  <pre
                    style={{
                      margin: 0,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontSize: 12,
                    }}
                  >
                    {testResult.result.sample_response}
                  </pre>
                }
              />
            ) : null}
          </Space>
        ) : null}
      </Modal>
    </div>
  );
}
