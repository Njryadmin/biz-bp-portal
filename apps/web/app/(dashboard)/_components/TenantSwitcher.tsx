// apps/web/app/(dashboard)/_components/TenantSwitcher.tsx
//
// M3 (2026-09-04) — super-admin tenant switcher.
//
// Renders a small button next to the TenantBadge. Click → modal with
// a list of every active tenant; selecting one writes
// `biz-bp.tenant_id` to localStorage and reloads so all subsequent
// /api/* calls carry X-Tenant-ID and resolve to the target tenant.
//
// The button is rendered only for users whose CurrentUser says
// they're super admin. For everyone else, the button is hidden and
// the TenantBadge alone is enough.

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { App, Button, List, Modal, Tag, Typography } from "antd";
import { SwapOutlined } from "@ant-design/icons";
import type { TenantInfo } from "@biz-bp/types";

import { listTenants } from "../../../lib/tenants";
import { readTenantId, writeTenantId } from "../../../lib/api";

const { Text } = Typography;

const PLAN_COLORS: Record<string, string> = {
  standard: "blue",
  enterprise: "gold",
  demo: "purple",
};

export function TenantSwitcher() {
  const router = useRouter();
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [tenants, setTenants] = useState<TenantInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);

  useEffect(() => {
    setActiveId(readTenantId());
  }, []);

  const loadTenants = async () => {
    setLoading(true);
    try {
      const data = await listTenants();
      setTenants(data.tenants);
    } catch (e) {
      message.error(`加载租户列表失败: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const openModal = () => {
    setOpen(true);
    loadTenants();
  };

  const selectTenant = (t: TenantInfo) => {
    writeTenantId(t.id);
    setActiveId(t.id);
    setOpen(false);
    message.success(`已切换到 ${t.name}`);
    // Reload so layout re-fetches the tenant badge + every server
    // component re-evaluates with the new X-Tenant-ID header.
    router.refresh();
  };

  const clearTenant = () => {
    writeTenantId(null);
    setActiveId(null);
    setOpen(false);
    message.info("已重置为默认租户 (跟随用户绑定)");
    router.refresh();
  };

  const activeTenant = tenants.find((t) => t.id === activeId);

  return (
    <>
      <Button
        size="small"
        icon={<SwapOutlined />}
        onClick={openModal}
        style={{
          background: "rgba(255,213,145,0.1)",
          border: "1px solid rgba(255,213,145,0.45)",
          color: "#ffd591",
        }}
        aria-label="切换租户 (super admin)"
        title="切换租户 (super admin only)"
      >
        {activeTenant ? `租户: ${activeTenant.name}` : "切换租户"}
      </Button>
      <Modal
        title="切换租户 (super admin)"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={520}
      >
        <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
          选择一个租户,后续 API 请求会通过 <code>X-Tenant-ID</code> header
          切到该租户 (仅 super admin 可用)。
        </Text>
        {activeId ? (
          <Button
            size="small"
            onClick={clearTenant}
            style={{ marginBottom: 12 }}
            danger
          >
            清除覆盖,回到用户默认租户
          </Button>
        ) : null}
        <List
          loading={loading}
          dataSource={tenants}
          renderItem={(t) => (
            <List.Item
              key={t.id}
              actions={[
                <Button
                  key="pick"
                  type={t.id === activeId ? "primary" : "default"}
                  size="small"
                  onClick={() => selectTenant(t)}
                >
                  {t.id === activeId ? "当前选中" : "选中"}
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <span>
                    {t.name}{" "}
                    <Tag
                      color={PLAN_COLORS[t.plan] ?? "default"}
                      style={{ marginLeft: 4 }}
                    >
                      {t.plan}
                    </Tag>
                    {!t.is_active ? (
                      <Tag color="red" style={{ marginLeft: 4 }}>
                        已停用
                      </Tag>
                    ) : null}
                  </span>
                }
                description={
                  <span style={{ fontSize: 12 }}>
                    <code>{t.slug}</code> · {t.user_count ?? 0} 用户 ·{" "}
                    {t.business_line_count ?? 0} 业务线
                  </span>
                }
              />
            </List.Item>
          )}
        />
      </Modal>
    </>
  );
}
