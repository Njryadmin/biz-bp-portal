// apps/web/lib/business-lines.ts
//
// 浏览器端辅助函数 — 业务线 admin 编辑器 (D2).
// 模式与 apps/web/lib/ai-models.ts 完全一致: 复用一个基于 cookie 的
// `apiJson` 包装, 让 fetch 自动带 credentials + content-type, 并把
// 4xx/5xx 翻译成带 status 的 Error 抛给上层页面处理.
//
// 上游 BFF:
//   GET    /api/admin/business-lines              → list
//   GET    /api/admin/business-lines/{line_id}    → 单个完整 manifest
//   PATCH  /api/admin/business-lines/{line_id}    → partial update
//
// 上游 Python: apps/api/app/routers/admin_business_lines.py (D1, 2026-09-04).

import type {
  BusinessLineFull,
  BusinessLineListResponse,
  BusinessLineSummary,
  UpdateBusinessLinePayload,
} from "@biz-bp/types";

async function readJson(res: Response) {
  if (res.status === 204) return null;
  return res.json().catch(() => null);
}

/**
 * Shared JSON-fetch wrapper used by the three admin business-line
 * endpoints. Mirrors `apiJson` in apps/web/lib/auth.ts and
 * apps/web/lib/ai-models.ts:
 *  - always sends `credentials: "include"` so the JWT cookie travels;
 *  - sets `Content-Type: application/json` for non-GET requests;
 *  - on non-2xx, reads the upstream `{ detail }` (FastAPI default) and
 *    throws an Error whose `.status` is the HTTP code — the page can
 *    branch on 404 (业务线不存在) vs 422 (字段校验失败) without parsing
 *    the body twice.
 */
async function apiJson<T>(input: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(input, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = (await readJson(res)) as { detail?: unknown } | null;
    let detail: string;
    if (typeof body?.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body?.detail)) {
      // FastAPI 422 returns a list of error objects; surface them as
      // a single newline-joined string so the toast stays readable.
      detail = (body!.detail as unknown[])
        .map((d) => (typeof d === "string" ? d : JSON.stringify(d)))
        .join("\n");
    } else {
      detail = `${init.method ?? "GET"} ${input} failed (HTTP ${res.status})`;
    }
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return ((await readJson(res)) as T) ?? ({} as T);
}

/**
 * List all business lines (summary view, includes v2 metadata flags).
 * Used by the admin list page.
 */
export async function listBusinessLines(): Promise<BusinessLineSummary[]> {
  const data = await apiJson<BusinessLineListResponse>(
    "/api/admin/business-lines",
    { method: "GET" },
  );
  return data.lines ?? [];
}

/**
 * Read the full manifest (v1 + v2) + indicators + charts for a single
 * business line. Used by the admin edit page. Throws an Error with
 * `.status === 404` if the line id does not exist.
 */
export async function getBusinessLine(id: string): Promise<BusinessLineFull> {
  const lineId = encodeURIComponent(id);
  return apiJson<BusinessLineFull>(
    `/api/admin/business-lines/${lineId}`,
    { method: "GET" },
  );
}

/**
 * Patch a business line manifest. Every field in `patch` is optional;
 * the server only touches keys present in the body. `indicators` and
 * `charts` are full replacements (not deltas) — see
 * `UpdateBusinessLinePayload` in packages/types/src/index.ts.
 *
 * On success, returns the same shape as `getBusinessLine(id)` so the
 * caller can re-render without a follow-up GET.
 */
export async function updateBusinessLine(
  id: string,
  patch: UpdateBusinessLinePayload,
): Promise<BusinessLineFull> {
  const lineId = encodeURIComponent(id);
  return apiJson<BusinessLineFull>(
    `/api/admin/business-lines/${lineId}`,
    { method: "PATCH", body: JSON.stringify(patch) },
  );
}

// ---------------------------------------------------------------------------
// Pure helpers (no fetch) — used by the page components.
// ---------------------------------------------------------------------------

/**
 * The 5 v2 data domains. Centralised here (and on the server) so the
 * admin UI never has to hard-code the same list twice. Order is fixed
 * — used as the default when a manifest predates v2 and as the visual
 * order in the access-matrix tables.
 */
export const V2_DOMAINS = [
  "business",
  "finance",
  "hr",
  "client",
  "project",
] as const;
export type V2DomainOption = (typeof V2_DOMAINS)[number];

/**
 * Stable per-domain color used in Tag columns + Checkbox.Group.
 * Matches the spec the parent task gave (business=blue, finance=gold,
 * hr=green, client=purple, project=cyan).
 */
export function domainColor(d: string): string {
  switch (d) {
    case "business":
      return "blue";
    case "finance":
      return "gold";
    case "hr":
      return "green";
    case "client":
      return "purple";
    case "project":
      return "cyan";
    default:
      return "default";
  }
}

export function domainLabel(d: string): string {
  switch (d) {
    case "business":
      return "business";
    case "finance":
      return "finance";
    case "hr":
      return "hr";
    case "client":
      return "client";
    case "project":
      return "project";
    default:
      return d;
  }
}

/**
 * The 4 line-scoped v2 roles that appear in `access_matrix`. The
 * global roles (admin/auditor/viewer/fin_bp_global/hr_bp_global)
 * are always full-access and never shown in the matrix.
 */
export const V2_ACCESS_ROLES = [
  "fin_bp",
  "hr_bp",
  "line_owner",
  "line_member",
] as const;
export type V2AccessRoleOption = (typeof V2_ACCESS_ROLES)[number];

/** Per-role label used in the access-matrix table. */
export function accessRoleLabel(r: string): string {
  switch (r) {
    case "fin_bp":
      return "fin_bp (业务线 FIN)";
    case "hr_bp":
      return "hr_bp (业务线 HR)";
    case "line_owner":
      return "line_owner (业务线总监)";
    case "line_member":
      return "line_member (普通员工)";
    default:
      return r;
  }
}

/** Per-role color used in the access-matrix table. */
export function accessRoleColor(r: string): string {
  switch (r) {
    case "fin_bp":
      return "lime";
    case "hr_bp":
      return "green";
    case "line_owner":
      return "gold";
    case "line_member":
      return "default";
    default:
      return "default";
  }
}

/**
 * The 3 KPI view keys. Each is a list of BusinessLineKpiItem.
 */
export const V2_KPI_VIEWS = ["fin_view", "hr_view", "shared_view"] as const;
export type V2KpiViewKey = (typeof V2_KPI_VIEWS)[number];

/** Per-view label used in the KPI sub-tabs. */
export function kpiViewLabel(v: string): string {
  switch (v) {
    case "fin_view":
      return "FIN 视角 (fin_view)";
    case "hr_view":
      return "HR 视角 (hr_view)";
    case "shared_view":
      return "共享视角 (shared_view)";
    default:
      return v;
  }
}

/**
 * The default access-matrix used to pre-fill the editor when the
 * manifest predates v2 (the server returns these too, but we want a
 * client-side default for the "Reset to v2 default" button).
 * Mirrors `business_lines/_template/manifest.yaml.v2.example`:
 *   fin_bp      → [business, finance, project]
 *   hr_bp       → [business, hr, client]
 *   line_owner  → [5 域全开]
 *   line_member → [business, project, client]
 */
export const DEFAULT_ACCESS_MATRIX: Record<string, string[]> = {
  fin_bp: ["business", "finance", "project"],
  hr_bp: ["business", "hr", "client"],
  line_owner: ["business", "finance", "hr", "client", "project"],
  line_member: ["business", "project", "client"],
};

/**
 * Validate a `<role>:<line_id>` string. Returns true on success.
 * Used for soft client-side validation in the owner_role_assignments
 * inputs; the server is still the source of truth.
 */
export function isValidRoleBinding(s: string): boolean {
  if (!s) return false;
  const idx = s.indexOf(":");
  if (idx <= 0 || idx === s.length - 1) return false;
  const role = s.slice(0, idx).trim();
  const lineId = s.slice(idx + 1).trim();
  if (!role || !lineId) return false;
  // Allow only the 3 line-scoped roles the manifest actually supports
  // in this field. Global roles don't need a line_id, so they never
  // appear here.
  return role === "fin_bp" || role === "hr_bp" || role === "line_owner";
}
