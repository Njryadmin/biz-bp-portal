// apps/web/app/403/page.tsx
//
// Friendly "forbidden" page shown when a logged-in user tries to
// access a resource they don't have permission for (e.g. a BP
// navigating to another business line's data via a deep link).

"use client";

import Link from "next/link";
import { Button, Result } from "antd";

export default function ForbiddenPage() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#f5f5f5",
        padding: 16,
      }}
    >
      <Result
        status="403"
        title="403"
        subTitle="抱歉,您没有访问该页面的权限。"
        extra={
          <Link href="/dashboard">
            <Button type="primary">回到首页</Button>
          </Link>
        }
      />
    </div>
  );
}
