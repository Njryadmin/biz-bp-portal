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

/* ----------------------- Admin: business lines (D1) ----------------------- */

// 5 v2 data domains (see apps/api/app/core/rbac_v2.py:DataDomain).
export type V2DataDomain = "business" | "finance" | "hr" | "client" | "project";

// 4 v2 keys allowed in the access_matrix (line-scoped roles only).
// Global roles (admin/auditor/viewer/fin_bp_global/hr_bp_global) are
// always full-access and never appear here.
export type V2AccessRole =
  | "fin_bp"
  | "hr_bp"
  | "line_owner"
  | "line_member";

// data_scope.domains subset (defaults to all 5 when the manifest predates v2).
export interface BusinessLineDataScope {
  domains: V2DataDomain[];
}

// owner_role_assignments: maps a v2 role to its "<role>:<line_id>"
// string. These are HINTS for the admin UI — the real user→role
// bindings live in the DB (user_roles table).
export interface BusinessLineOwnerRoleAssignments {
  finance_bp?: string;   // e.g. "fin_bp:residential"
  hr_bp?: string;        // e.g. "hr_bp:residential"
  line_owner?: string;   // e.g. "line_owner:residential"
}

// access_matrix: each key is a line-scoped v2 role; each value is
// the subset of the 5 data domains that role can see.
export interface BusinessLineAccessMatrix {
  fin_bp?: V2DataDomain[];
  hr_bp?: V2DataDomain[];
  line_owner?: V2DataDomain[];
  line_member?: V2DataDomain[];
}

// Single KPI definition (one of fin_view / hr_view / shared_view).
export interface BusinessLineKpiItem {
  id: string;
  title: string;
  source?: string;   // mart table, optional
  formula?: string;  // derived metric expression, optional
}

// kpis block: three lists, one per viewpoint.
export interface BusinessLineKpis {
  fin_view: BusinessLineKpiItem[];
  hr_view: BusinessLineKpiItem[];
  shared_view: BusinessLineKpiItem[];
}

// GET /api/admin/business-lines — one row per business line.
export interface BusinessLineSummary {
  id: string;
  name: string;
  version: string;
  description: string;
  owner: string;
  icon: string;
  /** Optional — only present when the manifest has v2 data_scope. */
  data_scope?: BusinessLineDataScope;
  /** Computed server-side at list time. */
  indicators_count: number;
  /** True if the on-disk manifest has any of the 4 v2 blocks. */
  has_v2_fields: boolean;
}

// GET /api/admin/business-lines/{id} — full record (manifest + indicators).
export interface BusinessLineFull extends BusinessLineSummary {
  // v1 technical
  api_prefix: string;
  warehouse: { schema: string; dbt_schema: string; mart_schema: string };
  refresh: { schedule: string; enabled: boolean };
  features: { universal_kpi: boolean; universal_chart: boolean; ag_grid: boolean };
  nav: BusinessLineNavItem[];
  // v2 (optional — defaults to {}/[]/{}/etc. when the manifest predates v2)
  owner_role_assignments?: BusinessLineOwnerRoleAssignments;
  access_matrix?: BusinessLineAccessMatrix;
  kpis?: BusinessLineKpis;
  // indicators — read from indicators.yaml next to the manifest
  indicators: Array<{
    id: string;
    title: string;
    unit: string;
    format: string;
    aggregation: string;
    source: string;
    description: string;
  }>;
  charts: Array<{
    id: string;
    title: string;
    type: string;
    x: string;
    y: string[];
    source: string;
    description: string;
  }>;
}

// PATCH /api/admin/business-lines/{id} body. Every field is optional;
// only the keys present in the body are touched. indicators / charts
// are full replacements (NOT deltas).
export interface UpdateBusinessLinePayload {
  // v1 human-readable
  name?: string;
  description?: string;
  owner?: string;
  icon?: string;
  // v1 technical
  api_prefix?: string;
  // v2
  data_scope?: BusinessLineDataScope;
  owner_role_assignments?: BusinessLineOwnerRoleAssignments;
  access_matrix?: BusinessLineAccessMatrix;
  kpis?: BusinessLineKpis;
  // indicators / charts — full replacement
  indicators?: BusinessLineFull["indicators"];
  charts?: BusinessLineFull["charts"];
}

// GET /api/admin/business-lines response.
export interface BusinessLineListResponse {
  count: number;
  lines: BusinessLineSummary[];
}

/* ----------------------------- Dashboard (E) ----------------------------- */

// Per-perspective dashboard response. Mirrors
// apps/api/app/schemas/dashboard.py:DashboardResponse.
export type DashboardView = "fin" | "hr" | "shared";

export interface DashboardKpiItem {
  line_id: string;
  kpi_id: string;
  title: string;
  value: number;
  unit?: string;
  trend?: string; // e.g. "+5%" / "-3%" / "—"
  source?: string | null;
  formula?: string | null;
}

export interface DashboardLine {
  line_id: string;
  line_name: string;
  kpi_count: number;
}

export interface DashboardResponse {
  view: DashboardView;
  kpis: DashboardKpiItem[];
  lines: DashboardLine[];
}

/* --------------------------- v2 me (E) ----------------------------------- */

// v2 user shape returned by GET /api/auth/me-v2.
// Mirrors apps/api/app/routers/auth.py:me_v2.
export type V2UserScope = "global" | "business_line";

export interface V2UserRoleBinding {
  role: string;
  scope: V2UserScope;
  line_id: string | null;
}

export interface V2CurrentUser {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  is_active: boolean;
  roles: string[];
  accessible_lines: string[];
  bindings: V2UserRoleBinding[];
  active_view: string | null;
}
