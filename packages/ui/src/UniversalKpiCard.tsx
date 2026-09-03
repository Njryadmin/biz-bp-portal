// packages/ui/src/UniversalKpiCard.tsx
//
// Universal indicator card. Completely generic — the only domain concept it
// knows about is the `Indicator` shape (id / name / unit / format). The card
// renders the current value (formatted per `format`), a `trend` (period-over-
// period percent) and a tiny inline-SVG sparkline.

import { Card, Skeleton, Tooltip } from "antd";
import type { CSSProperties } from "react";
import type { IndicatorFormat } from "@biz-bp/types";

export interface UniversalKpiIndicator {
  id: string;
  name: string;
  unit?: string;
  format?: IndicatorFormat;
}

export interface UniversalKpiCardProps {
  indicator: UniversalKpiIndicator;
  value: number | null | undefined;
  /**
   * Optional period-over-period change as a decimal ratio (e.g. 0.12 = +12%).
   * When provided, an arrow + color is rendered next to the value.
   */
  delta?: number | null;
  /**
   * Optional trend direction. If `delta` is not provided, `trend` alone only
   * influences the arrow direction and color but not the displayed percent.
   * `trend` accepts either a numeric ratio or one of the string shortcuts.
   */
  trend?: number | "up" | "down" | "flat" | null;
  /**
   * Optional sparkline series. When provided, a tiny inline SVG line is drawn
   * behind / under the value. Order is left-to-right.
   */
  sparkline?: number[];
  /** Loading state — renders a skeleton instead of a value. */
  loading?: boolean;
  /** Decimal precision for the formatted value. */
  precision?: number;
  /** Card style override. */
  style?: CSSProperties;
}

function formatValue(
  value: number | null | undefined,
  format: IndicatorFormat | undefined,
  precision: number,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  switch (format) {
    case "currency":
      return value.toLocaleString("zh-CN", {
        style: "currency",
        currency: "CNY",
        minimumFractionDigits: 0,
        maximumFractionDigits: precision,
      });
    case "percent":
      return `${(value * 100).toFixed(precision)}%`;
    case "ratio":
      return value.toFixed(precision);
    case "number":
    default:
      return value.toLocaleString("zh-CN", { maximumFractionDigits: precision });
  }
}

function resolveDeltaDirection(
  delta: number | null | undefined,
  trend: number | "up" | "down" | "flat" | null | undefined,
): "up" | "down" | "flat" {
  if (typeof trend === "string" && (trend === "up" || trend === "down" || trend === "flat")) {
    return trend;
  }
  if (typeof delta === "number" && !Number.isNaN(delta)) {
    if (delta > 0) return "up";
    if (delta < 0) return "down";
    return "flat";
  }
  if (typeof trend === "number" && !Number.isNaN(trend)) {
    if (trend > 0) return "up";
    if (trend < 0) return "down";
    return "flat";
  }
  return "flat";
}

const DIR_COLOR: Record<"up" | "down" | "flat", string> = {
  up: "#3f8600",
  down: "#cf1322",
  flat: "#8c8c8c",
};

const DIR_GLYPH: Record<"up" | "down" | "flat", string> = {
  up: "▲",
  down: "▼",
  flat: "■",
};

interface SparklineProps {
  data: number[];
  color: string;
  width?: number;
  height?: number;
}

function Sparkline({ data, color, width = 120, height = 28 }: SparklineProps) {
  if (!data || data.length < 2) {
    return <span style={{ display: "inline-block", width, height }} />;
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const stepX = width / (data.length - 1);
  const points = data
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="sparkline"
      style={{ display: "block" }}
    >
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function UniversalKpiCard({
  indicator,
  value,
  delta,
  trend,
  sparkline,
  loading = false,
  precision = 2,
  style,
}: UniversalKpiCardProps) {
  const direction = resolveDeltaDirection(delta, trend);
  const color = DIR_COLOR[direction];
  const glyph = DIR_GLYPH[direction];
  const hasDelta = typeof delta === "number" && !Number.isNaN(delta);
  const showTrend = hasDelta || (trend !== undefined && trend !== null);

  const formatted = formatValue(value, indicator.format, precision);
  const unitSuffix = indicator.unit && indicator.unit.length > 0 ? ` ${indicator.unit}` : "";

  return (
    <Card size="small" style={{ minWidth: 200, ...style }} styles={{ body: { padding: 16 } }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ fontSize: 12, color: "#8c8c8c", textTransform: "uppercase", letterSpacing: 0.4 }}>
          {indicator.name}
        </div>
        {showTrend && !loading ? (
          <Tooltip
            title={
              hasDelta
                ? `Period-over-period: ${(delta! * 100).toFixed(2)}%`
                : "Trend direction"
            }
          >
            <span style={{ fontSize: 12, color, fontWeight: 600 }}>
              {glyph}
              {hasDelta ? ` ${Math.abs(delta!).toFixed(2)}%` : ""}
            </span>
          </Tooltip>
        ) : null}
      </div>

      {loading ? (
        <Skeleton.Node active style={{ width: "100%", height: 36, marginTop: 8 }}>
          <span />
        </Skeleton.Node>
      ) : (
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginTop: 8 }}>
          <div style={{ fontSize: 24, fontWeight: 600, color: "#262626" }}>
            {formatted}
            {unitSuffix ? (
              <span style={{ fontSize: 12, color: "#8c8c8c", marginLeft: 4 }}>{unitSuffix}</span>
            ) : null}
          </div>
          {sparkline && sparkline.length > 1 ? (
            <Sparkline data={sparkline} color={color} />
          ) : null}
        </div>
      )}
    </Card>
  );
}
