// packages/types/src/index.ts
// Shared TypeScript types for the fin-bp-portal monorepo.
// Consumed by both apps/web and apps/api (openapi-typescript generated types
// can be added later by extending this barrel).

/* ----------------------------- Business Line ----------------------------- */

export interface BusinessLineNavItem {
  path: string;
  title: string;
}

export interface BusinessLineWarehouse {
  schema: string;
  dbt_schema: string;
  mart_schema: string;
}

export interface BusinessLineRefresh {
  schedule: string;
  enabled: boolean;
}

export interface BusinessLineFeatures {
  universal_kpi: boolean;
  universal_chart: boolean;
  ag_grid: boolean;
}

export interface BusinessLine {
  id: string;
  name: string;
  /**
   * Human-readable name returned by the registry. Currently identical to
   * `name`, but exposed as a separate field so the cockpit layout can be
   * wired against a stable, locale-aware display string in the future.
   * Optional for backward compatibility with manifests that predate this field.
   */
  display_name?: string;
  version: string;
  description: string;
  owner: string;
  icon: string;
  nav: BusinessLineNavItem[];
  api_prefix: string;
  warehouse: BusinessLineWarehouse;
  refresh: BusinessLineRefresh;
  features: BusinessLineFeatures;
  /**
   * Number of indicators registered for this line. Computed server-side at
   * list time so the cockpit can render count badges without a per-line
   * fetch. Optional for backward compatibility.
   */
  indicators_count?: number;
}

/* ------------------------------ Indicators ------------------------------- */

export type IndicatorFormat = "currency" | "number" | "percent" | "ratio";
export type IndicatorAggregation = "sum" | "avg" | "count" | "count_distinct" | "min" | "max";

export interface Indicator {
  id: string;
  title: string;
  unit: string;
  format: IndicatorFormat;
  aggregation: IndicatorAggregation;
  source: string;
  description: string;
}

export type ChartType = "line" | "bar" | "pie" | "area";

export interface ChartSpec {
  id: string;
  title: string;
  type: ChartType;
  x: string;
  y: string[];
  source: string;
  description: string;
}

/* --------------------------------- KPI ----------------------------------- */

export interface KpiValue {
  indicator_id: string;
  value: number | null;
  period_start?: string;
  period_end?: string;
  unit?: string;
}

export interface KpiResponse {
  line_id: string;
  items: KpiValue[];
}
