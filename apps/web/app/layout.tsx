// apps/web/app/layout.tsx
// Next.js 应用根布局。注入 AntdRegistry（antd 在 Next.js App Router
// 下的样式注册组件）以及 ConfigProvider（统一主题 token），所有
// 页面都嵌套在这个根布局之下。
import type { ReactNode } from "react";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { ConfigProvider } from "antd";
import "antd/dist/reset.css";

export const metadata = {
  title: "Biz-BP Portal",
  description: "Financial Business Performance Portal",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body style={{ margin: 0 }}>
        <AntdRegistry>
          <ConfigProvider
            theme={{
              token: {
                colorPrimary: "#1677ff",
                borderRadius: 6,
              },
            }}
          >
            {children}
          </ConfigProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
