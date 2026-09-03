// apps/web/app/login/page.tsx
//
// Full-screen login form. Redirects to ``from`` (the page the user
// tried to reach) on successful auth, or to ``/dashboard`` by default.
//
// Behavior
// --------
// 1. The user lands on /login?from=/some/path → we store the from
//    in component state and call POST /api/auth/login via the BFF.
// 2. On success, the BFF sets the httpOnly cookie; we read /api/auth/me
//    to confirm and then router.push(from).
// 3. On failure, we show the API error message and keep the form.

"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Alert, Button, Form, Input, Typography } from "antd";
import { LockOutlined, UserOutlined } from "@ant-design/icons";

const { Title, Text } = Typography;

function LoginInner() {
  const router = useRouter();
  const search = useSearchParams();
  const from = search.get("from") || "/dashboard";
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onFinish(values: { username: string; password: string }) {
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
        credentials: "include",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(
          data?.detail ||
            "登录失败,请检查用户名和密码",
        );
        setSubmitting(false);
        return;
      }
      // Confirm cookie by calling /me (defensive — the cookie is
      // already set by the BFF, this also serves as a connectivity
      // check to the API).
      const meRes = await fetch("/api/auth/me", { credentials: "include" });
      if (!meRes.ok) {
        setError("登录后端异常,请稍后重试");
        setSubmitting(false);
        return;
      }
      // Redirect.
      router.push(from);
      router.refresh();
    } catch (e) {
      setError(`网络错误: ${(e as Error).message}`);
      setSubmitting(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #001529 0%, #1f3247 100%)",
        padding: 16,
      }}
    >
      <div
        style={{
          width: 380,
          maxWidth: "100%",
          background: "#fff",
          borderRadius: 8,
          padding: "32px 28px",
          boxShadow: "0 4px 24px rgba(0,0,0,0.15)",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <Title level={3} style={{ margin: 0 }}>
            Fin BP Portal
          </Title>
          <Text type="secondary">登录以访问业务线数据</Text>
        </div>
        {error ? (
          <Alert
            type="error"
            message={error}
            style={{ marginBottom: 16 }}
            closable
            onClose={() => setError(null)}
          />
        ) : null}
        <Form
          name="login"
          onFinish={onFinish}
          autoComplete="on"
          layout="vertical"
          requiredMark={false}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input
              prefix={<UserOutlined />}
              placeholder="admin / bp-residential / viewer"
              autoFocus
              autoComplete="username"
              size="large"
            />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="••••••••"
              autoComplete="current-password"
              size="large"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, marginTop: 8 }}>
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              block
              loading={submitting}
            >
              登录
            </Button>
          </Form.Item>
        </Form>
        <div style={{ marginTop: 16, fontSize: 12, color: "#888" }}>
          <Text type="secondary">
            默认账号 (生产请修改): admin / admin123
          </Text>
          <br />
          <Text type="secondary">
            业务线 BP 用户: bp-&lt;line&gt; / bp123456
          </Text>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div style={{ padding: 40, textAlign: "center" }}>Loading…</div>
      }
    >
      <LoginInner />
    </Suspense>
  );
}
