// packages/ui/src/UniversalChart.tsx
//
// Universal chart factory. Picks the right ECharts 5 series type for the given
// `type` prop and renders a `<ReactECharts />`. The contract is intentionally
// minimal: `data` shape and an optional `options` escape hatch for full
// ECharts option overrides.
//
// Supported types:
//   - line:       [{x, y, series?}]  or  { categories, values, series? }
//   - bar:        same as line
//   - scatter:    [{x, y, size?, group?}]  (size maps to symbolSize)
//   - waterfall:  { categories, values, total? } — values may include explicit
//                 negatives, or values may include named 'subtract' markers via
//                 {value, isSubtract:true}; this module computes the stacked
//                 "placeholder + delta" series automatically.
//   - heatmap:    { xCategories, yCategories, matrix: number[][] }
//
// `data` is intentionally loosely typed; the per-type normalizer coerces it
// to the ECharts-friendly shape.

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { Skeleton } from "antd";
import type { CSSProperties } from "react";

export type UniversalChartType = "line" | "bar" | "scatter" | "waterfall" | "heatmap";

export interface UniversalChartDataPoint {
  x: string | number;
  y: number;
  size?: number;
  group?: string;
  isSubtract?: boolean;
}

export interface UniversalChartSeriesData {
  /** X-axis category labels (line / bar). */
  categories?: (string | number)[];
  /** Parallel values to `categories` (line / bar) OR flat cell values (heatmap). */
  values?: number[];
  /** Multi-series name list; when present, `values` is split evenly per series. */
  series?: (string | number)[];
  /** Heatmap: x-axis category labels. */
  xCategories?: (string | number)[];
  /** Heatmap: y-axis category labels. */
  yCategories?: (string | number)[];
  /** Heatmap: 2-D matrix of cell values (yRows × xCols). */
  matrix?: number[][];
  // Catch-all for "anything that can be normalized" — the per-type normalizer
  // is responsible for projecting this into ECharts-shaped series.
  points?: UniversalChartDataPoint[];
}

export interface UniversalChartOptions {
  title?: string;
  yAxisLabel?: string;
  xAxisLabel?: string;
  height?: number;
  showLegend?: boolean;
  // Free-form ECharts overrides; merged last so consumers can tweak anything.
  echartsOverrides?: Record<string, unknown>;
}

export interface UniversalChartProps {
  type: UniversalChartType;
  data: UniversalChartSeriesData | UniversalChartDataPoint[];
  options?: UniversalChartOptions;
  loading?: boolean;
  style?: CSSProperties;
  notMerge?: boolean;
}

const DEFAULT_HEIGHT = 320;

function asArray(data: UniversalChartProps["data"]): UniversalChartDataPoint[] {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.points)) return data.points;
  return [];
}

function asSeriesData(data: UniversalChartProps["data"]): UniversalChartSeriesData {
  if (data && !Array.isArray(data)) return data as UniversalChartSeriesData;
  return {};
}

function buildLineOrBarOption(
  type: "line" | "bar",
  data: UniversalChartProps["data"],
  options: UniversalChartOptions | undefined,
): Record<string, unknown> {
  const seriesData = asSeriesData(data);
  const points = asArray(data);

  // Multi-series when `series` is present, else single series.
  let categories: (string | number)[];
  let series: { name: string; data: number[] }[];

  if (seriesData.series && seriesData.series.length > 0 && seriesData.values) {
    // Treat values[] as parallel to series[] (one value per series per index).
    categories = seriesData.categories ?? points.map((p) => p.x);
    const nSeries = seriesData.series.length;
    const nPoints = seriesData.values.length / nSeries;
    series = Array.from({ length: nSeries }, (_, s) => ({
      name: String(seriesData.series![s]),
      data: seriesData.values!.slice(s * nPoints, (s + 1) * nPoints),
    }));
  } else if (seriesData.categories && seriesData.values) {
    categories = seriesData.categories;
    series = [{ name: options?.title ?? "value", data: seriesData.values }];
  } else {
    // Fall back to a flat points array.
    categories = points.map((p) => p.x);
    series = [
      {
        name: options?.title ?? "value",
        data: points.map((p) => p.y),
      },
    ];
  }

  return {
    color: ["#1677ff", "#52c41a", "#fa8c16", "#722ed1"],
    title: options?.title
      ? { text: options.title, left: "left", textStyle: { fontSize: 14, fontWeight: 600 } }
      : undefined,
    tooltip: { trigger: "axis" },
    legend: options?.showLegend === false ? undefined : { type: "plain", top: 0, right: 0 },
    grid: { left: 48, right: 24, top: options?.title ? 48 : 32, bottom: 48, containLabel: true },
    xAxis: {
      type: "category",
      data: categories,
      name: options?.xAxisLabel,
      nameLocation: "middle",
      nameGap: 28,
    },
    yAxis: {
      type: "value",
      name: options?.yAxisLabel,
      nameLocation: "middle",
      nameGap: 40,
    },
    series: series.map((s) => ({
      name: s.name,
      type,
      data: s.data,
      smooth: type === "line",
      symbol: type === "line" ? "circle" : undefined,
      symbolSize: type === "line" ? 6 : undefined,
      barMaxWidth: type === "bar" ? 32 : undefined,
    })),
    ...(options?.echartsOverrides ?? {}),
  };
}

function buildScatterOption(
  data: UniversalChartProps["data"],
  options: UniversalChartOptions | undefined,
): Record<string, unknown> {
  const points = asArray(data);
  // Group by `group` if present, else single series.
  const groups = new Map<string, UniversalChartDataPoint[]>();
  for (const p of points) {
    const g = p.group ?? options?.title ?? "value";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)!.push(p);
  }
  const series = Array.from(groups.entries()).map(([name, pts]) => ({
    name,
    type: "scatter",
    data: pts.map((p) => {
      const obj: [number, number, number?] = [typeof p.x === "number" ? p.x : 0, p.y];
      // ECharts scatter accepts [x, y, symbolSize] when data is tuple.
      if (typeof p.size === "number" && !Number.isNaN(p.size)) obj.push(p.size);
      return obj;
    }),
    symbolSize: (val: number[]) => {
      // If individual point has size, use it; else derive from val[2] or default.
      const explicit = val[2];
      if (typeof explicit === "number") return Math.max(6, Math.min(40, explicit));
      return 12;
    },
  }));

  return {
    color: ["#1677ff", "#52c41a", "#fa8c16", "#722ed1", "#eb2f96"],
    title: options?.title
      ? { text: options.title, left: "left", textStyle: { fontSize: 14, fontWeight: 600 } }
      : undefined,
    tooltip: {
      trigger: "item",
      formatter: (p: { value: number[]; seriesName?: string }) =>
        `${p.seriesName ?? ""}<br/>x: ${p.value[0]}<br/>y: ${p.value[1]}${
          typeof p.value[2] === "number" ? `<br/>size: ${p.value[2]}` : ""
        }`,
    },
    legend: options?.showLegend === false ? undefined : { type: "plain", top: 0, right: 0 },
    grid: { left: 48, right: 24, top: options?.title ? 48 : 32, bottom: 48, containLabel: true },
    xAxis: { type: "value", name: options?.xAxisLabel, nameLocation: "middle", nameGap: 28 },
    yAxis: { type: "value", name: options?.yAxisLabel, nameLocation: "middle", nameGap: 40 },
    series,
    ...(options?.echartsOverrides ?? {}),
  };
}

function buildWaterfallOption(
  data: UniversalChartProps["data"],
  options: UniversalChartOptions | undefined,
): Record<string, unknown> {
  const seriesData = asSeriesData(data);
  const points = asArray(data);

  // Resolve (categories, rawValues) from either form.
  let categories: (string | number)[];
  let rawValues: number[];
  let subtractFlags: boolean[] | null = null;

  if (seriesData.categories && seriesData.values) {
    categories = seriesData.categories;
    rawValues = seriesData.values;
  } else if (points.length > 0) {
    categories = points.map((p) => p.x);
    rawValues = points.map((p) => p.y);
    if (points.some((p) => typeof p.isSubtract === "boolean")) {
      subtractFlags = points.map((p) => Boolean(p.isSubtract));
    }
  } else {
    categories = [];
    rawValues = [];
  }

  // Compute running total and per-bar placeholder + delta.
  const placeholder: (number | "-")[] = [];
  const delta: (number | "-")[] = [];
  const totalMark = options?.echartsOverrides?.total === true;
  let running = 0;
  for (let i = 0; i < rawValues.length; i++) {
    const v = rawValues[i];
    const isSub = subtractFlags ? subtractFlags[i] : v < 0;
    const abs = Math.abs(v);
    if (i === 0 && !isSub) {
      running = abs;
      placeholder.push(0);
      delta.push(abs);
    } else if (isSub) {
      // subtract: from running down to running - abs
      placeholder.push(running - abs);
      delta.push(-abs);
      running = running - abs;
    } else {
      // add: from running up to running + abs
      placeholder.push(running);
      delta.push(abs);
      running = running + abs;
    }
  }

  // Optional total bar at the end.
  if (totalMark) {
    categories.push("Total");
    placeholder.push(0);
    delta.push(running);
  }

  return {
    color: ["#1677ff", "#52c41a"],
    title: options?.title
      ? { text: options.title, left: "left", textStyle: { fontSize: 14, fontWeight: 600 } }
      : undefined,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: { axisValue: string | number; seriesName: string; value: number | "-" }[]) => {
        const lines = params.map(
          (p) => `${p.seriesName}: ${typeof p.value === "number" ? p.value : 0}`,
        );
        return `${params[0]?.axisValue ?? ""}<br/>${lines.join("<br/>")}`;
      },
    },
    legend: { data: ["Placeholder", "Delta"], top: 0, right: 0 },
    grid: { left: 48, right: 24, top: 48, bottom: 48, containLabel: true },
    xAxis: {
      type: "category",
      data: categories,
      name: options?.xAxisLabel,
      nameLocation: "middle",
      nameGap: 28,
    },
    yAxis: {
      type: "value",
      name: options?.yAxisLabel,
      nameLocation: "middle",
      nameGap: 40,
    },
    series: [
      // Invisible placeholder so each bar floats at the right level.
      {
        name: "Placeholder",
        type: "bar",
        stack: "total",
        itemStyle: { color: "transparent" },
        emphasis: { itemStyle: { color: "transparent" } },
        data: placeholder,
      },
      {
        name: "Delta",
        type: "bar",
        stack: "total",
        label: { show: true, position: "top", formatter: (p: { value: number | "-" }) => (typeof p.value === "number" ? String(p.value) : "") },
        data: delta,
        itemStyle: {
          color: (params: { dataIndex: number }) => {
            const v = delta[params.dataIndex];
            if (typeof v !== "number") return "#1677ff";
            return v >= 0 ? "#52c41a" : "#cf1322";
          },
        },
      },
    ],
    ...(options?.echartsOverrides ?? {}),
  };
}

function buildHeatmapOption(
  data: UniversalChartProps["data"],
  options: UniversalChartOptions | undefined,
): Record<string, unknown> {
  const seriesData = asSeriesData(data);
  const xCategories = seriesData.xCategories ?? [];
  const yCategories = seriesData.yCategories ?? [];
  const matrix = seriesData.matrix ?? [];

  // ECharts heatmap wants [xIdx, yIdx, value] tuples.
  const points: [number, number, number][] = [];
  for (let y = 0; y < matrix.length; y++) {
    for (let x = 0; x < matrix[y].length; x++) {
      const v = matrix[y][x];
      if (typeof v === "number" && !Number.isNaN(v)) {
        points.push([x, y, v]);
      }
    }
  }

  // Compute min/max for color scale.
  let min = Infinity;
  let max = -Infinity;
  for (const [, , v] of points) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (!Number.isFinite(min)) min = 0;
  if (!Number.isFinite(max)) max = 1;

  return {
    title: options?.title
      ? { text: options.title, left: "left", textStyle: { fontSize: 14, fontWeight: 600 } }
      : undefined,
    tooltip: {
      position: "top",
      formatter: (p: { value: [number, number, number] }) =>
        `${xCategories[p.value[0]] ?? p.value[0]} · ${yCategories[p.value[1]] ?? p.value[1]}: ${p.value[2]}`,
    },
    grid: { left: 96, right: 24, top: options?.title ? 48 : 16, bottom: 64, containLabel: true },
    xAxis: {
      type: "category",
      data: xCategories,
      splitArea: { show: true },
    },
    yAxis: {
      type: "category",
      data: yCategories,
      splitArea: { show: true },
    },
    visualMap: {
      min,
      max,
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 8,
      inRange: { color: ["#f0f5ff", "#1677ff", "#003a8c"] },
    },
    series: [
      {
        name: options?.title ?? "value",
        type: "heatmap",
        data: points,
        label: { show: false },
        emphasis: { itemStyle: { shadowBlur: 10, shadowColor: "rgba(0,0,0,0.3)" } },
      },
    ],
    ...(options?.echartsOverrides ?? {}),
  };
}

export function UniversalChart({
  type,
  data,
  options,
  loading = false,
  style,
  notMerge = true,
}: UniversalChartProps) {
  const height = options?.height ?? DEFAULT_HEIGHT;

  const option = useMemo<Record<string, unknown>>(() => {
    switch (type) {
      case "line":
      case "bar":
        return buildLineOrBarOption(type, data, options);
      case "scatter":
        return buildScatterOption(data, options);
      case "waterfall":
        return buildWaterfallOption(data, options);
      case "heatmap":
        return buildHeatmapOption(data, options);
      default:
        return {};
    }
  }, [type, data, options]);

  if (loading) {
    return (
      <Skeleton.Node
        active
        style={{ width: "100%", height, ...style }}
      />
    );
  }
  return (
    <ReactECharts
      option={option}
      notMerge={notMerge}
      lazyUpdate
      style={{ width: "100%", height, ...style }}
      opts={{ renderer: "canvas" }}
    />
  );
}
