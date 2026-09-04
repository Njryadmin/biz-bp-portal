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

/* ----------------------------- RBAC v2 ----------------------------------- */

// 8 v2 role ids (see apps/api/app/core/rbac_v2.py:Role). Defined as a
// literal-union type so the admin UI gets IntelliSense when picking
// from the dropdown. The set is duplicated in the Python router's
// ``_ROLE_ENUM_VALUES``; keep them in sync when adding a role.
export type V2Role =
  | "admin"
  | "auditor"
  | "viewer"
  | "line_owner"
  | "fin_bp"
  | "hr_bp"
  | "fin_bp_global"
  | "hr_bp_global";

// v2 scope literal-union. ``"legacy"`` is a fallback the API uses for
// rows whose scope column is NULL (i.e. migration 001 hasn't been run
// yet on that DB). The admin UI surfaces it as "needs migration".
export type V2Scope = "global" | "business_line" | "legacy";

// Single v2 role binding. Maps to apps/api/app/schemas/auth.py:
// UserRoleBindingResponse. The 4-tuple (role, scope, line_id) drives
// every permission check in the v2 router guards.
export interface UserRoleBinding {
  /** fin_bp / hr_bp / line_owner / admin / auditor / viewer / fin_bp_global / hr_bp_global */
  role: string;
  /** "global"  → line_id must be null; "business_line" → line_id required */
  scope: V2Scope;
  /** business-line id, required iff scope==="business_line" */
  line_id: string | null;
}

// PATCH /api/auth/users/{id}/v2-roles body. The list is a full
// replacement (not a patch); the router rejects empty arrays so at
// least one admin always remains in the system.
export interface UpdateUserV2RolesPayload {
  bindings: UserRoleBinding[];
}

// GET / PATCH /api/auth/users/{id}/v2-roles response. The admin UI
// re-renders directly from this without a follow-up GET.
export interface UserV2RolesResponse {
  user_id: number;
  bindings: UserRoleBinding[];
}
