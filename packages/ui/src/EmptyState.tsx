// packages/ui/src/EmptyState.tsx
//
// Generic empty state. The cockpit uses this for both "no business lines
// registered yet" and "no data for this indicator". An optional docs link
// turns the empty state into a call-to-action for adding new content.

import { Button, Empty, Space } from "antd";
import { BookOutlined } from "@ant-design/icons";
import type { CSSProperties } from "react";
import Link from "next/link";

export interface EmptyStateProps {
  title?: string;
  description?: string;
  /** Path to the docs page that explains how to add content. */
  docsHref?: string;
  /** Label of the docs CTA button. */
  docsLabel?: string;
  style?: CSSProperties;
}

export function EmptyState({
  title = "No data",
  description = "Nothing to show yet.",
  docsHref,
  docsLabel = "Read the plugin how-to",
  style,
}: EmptyStateProps) {
  return (
    <div style={{ padding: 32, textAlign: "center", ...style }}>
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space direction="vertical" size={4} style={{ marginTop: 8 }}>
            <span style={{ color: "#8c8c8c", fontSize: 14 }}>{title}</span>
            <span style={{ color: "#bfbfbf", fontSize: 12 }}>{description}</span>
          </Space>
        }
      >
        {docsHref ? (
          <Link href={docsHref} target="_blank" rel="noopener noreferrer">
            <Button type="primary" icon={<BookOutlined />}>
              {docsLabel}
            </Button>
          </Link>
        ) : null}
      </Empty>
    </div>
  );
}
