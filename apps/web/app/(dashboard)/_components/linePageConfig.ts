// apps/web/app/(dashboard)/_components/linePageConfig.ts
//
// Page-spec table for the dynamic business-line router
// (apps/web/app/(dashboard)/[line]/[page]/page.tsx).
//
// P2 #3 (2026-09-03): the spec table is NO LONGER hardcoded. It is
// built at runtime from the manifest-declared `nav[]` of every line
// in the registry, so adding a new business line (and its nav entries)
// to `business_lines/registry.yaml` automatically wires every page
// into the App Router — no edits here required.
//
// History
// -------
// The original version of this file hardcoded 4 lines (residential,
// retail, retail-leasing, my-line). The 6 new lines added in v2.2.0
// (valuation / advisory / office-leasing / investment /
// project-management / industrial) were silently routing to
// "not-integrated" even when their API endpoints were already live.
// This rewrite makes the table data-driven so the 6 new lines (and
// any future ones) get rendered for free.

import type { BusinessLine } from "@biz-bp/types";

export type PageKind =
  | "line-overview"
  | "property-detail"
  | "project-detail"
  | "market-benchmark"
  | "vacancy-alerts"
  | "properties-summary"
  | "ping-only"
  | "not-integrated";

export interface PageSpec {
  kind: PageKind;
  /** Sub-endpoint to call after resolving the first id from the list. */
  detailEndpoint?: string;
  /** Human-readable title from manifest.yaml nav[]. Used by the UI. */
  title?: string;
}

type LinePageMap = Record<string, Record<string, PageSpec>>;

// ---------------------------------------------------------------------------
// Heuristics: turn a manifest nav path into a render kind.
// ---------------------------------------------------------------------------
//
// The manifest only declares the *path* (e.g. "/valuation/reports") and
// the *title* (e.g. "报告明细"). It does NOT declare which render kind
// to use, because that's a UI concern, not a data-model concern. We
// infer the kind from the URL slug using a small, well-known
// keyword table. This is a deliberate design choice — the alternative
// (adding a `kind:` field to every nav entry) would have made manifests
// noisier and required an explicit edit per page. The keyword table
// below covers every page slug shipped today and falls through to
// "properties-summary" / "project-detail" based on which collection
// root the line exposes.
//
// To override a guess, line authors can add a slug to
// `KNOWN_KIND_OVERRIDES` below (e.g. for a custom dashboard). All other
// pages inherit the heuristic.
//
// ---------------------------------------------------------------------------

/** Per-line kind overrides keyed by `${lineId}:${slug}`. */
const KNOWN_KIND_OVERRIDES: Record<string, PageKind> = {
  // retail-leasing: nav paths are unique to this line
  "retail-leasing:market-report": "market-benchmark",
  "retail-leasing:vacancy-alert": "vacancy-alerts",
  "retail-leasing:leasing-kpi": "properties-summary",
  // my-line: special "ping" homepage + nav stub
  "my-line:ping": "not-integrated", // /ping is a health probe, not a UI page
  // my-line "" (root) is handled below — set to ping-only via the
  // top-level line-level override.
};

/** Per-line top-level overview kind. Line root page only. */
const KNOWN_OVERVIEW_OVERRIDES: Record<string, PageKind> = {
  "my-line": "ping-only",
};

/**
 * Slug → default PageKind. Read as: "if the nav path contains this
 * slug, render with this kind". Order matters — first match wins.
 */
const SLUG_KIND_RULES: Array<{ match: RegExp; kind: PageKind }> = [
  { match: /market[-_]?report|market[-_]?benchmark|benchmark/i, kind: "market-benchmark" },
  { match: /vacanc|空置/i, kind: "vacancy-alerts" },
  { match: /leasing[-_]?kpi|leasing[-_]?summary|商铺.*总览|物业.*总览/i, kind: "properties-summary" },
  { match: /report|accuracy|collection|appraiser|kpi|indicator|业绩|报告|偏差|回款|人均|客户|行业|产能|续约|团队|项目.*明细|portfolio|projects|组合|基金|投资/i, kind: "project-detail" },
  { match: /noi|brand|renovat|collection[-_]?rate|efficiency/i, kind: "property-detail" },
];

/**
 * Pick a render kind for a (line, slug, nav_title) triple. Pure
 * function — no I/O, easy to unit test.
 *
 * Resolution order:
 *   1. Explicit per-line override
 *   2. Heuristic on the slug + nav title
 *   3. Default to "project-detail" (the most common kind — covers
 *      reports, KPIs, per-resource drill-downs).
 *
 * @param line        The BusinessLine from the registry.
 * @param slug        The URL slug (e.g. "reports"). Empty string for the
 *                    line root.
 * @param navTitle    The manifest-declared nav title (e.g. "报告明细").
 *                    Used as a tie-breaker for the heuristic.
 */
export function inferPageKind(
  line: BusinessLine,
  slug: string,
  navTitle: string,
): PageKind {
  if (slug === "") {
    return KNOWN_OVERVIEW_OVERRIDES[line.id] ?? "line-overview";
  }
  const overrideKey = `${line.id}:${slug}`;
  if (KNOWN_KIND_OVERRIDES[overrideKey]) {
    return KNOWN_KIND_OVERRIDES[overrideKey];
  }
  // For the "default" page (first nav entry) of a non-overview line,
  // also check the overview override — but only if the slug matches
  // the line root path.
  if (slug === "" || slug === line.id) {
    return KNOWN_OVERVIEW_OVERRIDES[line.id] ?? "line-overview";
  }
  // Try each rule in order; slug + title both count so CJK titles
  // like "估价偏差" also match the bias rule.
  for (const rule of SLUG_KIND_RULES) {
    if (rule.match.test(slug) || rule.match.test(navTitle)) {
      return rule.kind;
    }
  }
  // Heuristic for the detail endpoint: if the line exposes /properties
  // (i.e. has any "property-detail" feel — retail / retail-leasing /
  // office-leasing) prefer "property-detail"; otherwise default to
  // "project-detail".
  const hasPropertiesKind = (line.id === "retail" || line.id === "retail-leasing" || line.id === "office-leasing");
  if (hasPropertiesKind) {
    return "property-detail";
  }
  return "project-detail";
}

// ---------------------------------------------------------------------------
// The dynamic builder.
// ---------------------------------------------------------------------------

/**
 * Build the {line_id: {slug: PageSpec}} map from a list of business
 * lines (typically the response of /api/registry/lines).
 *
 * For each line we iterate its manifest `nav[]` and produce one entry
 * per nav item:
 *   - slug ``""``  (line root)  → line-overview (or ping-only for my-line)
 *   - slug `"foo"` (sub-page)   → kind inferred from slug + title
 *
 * `detailEndpoint` is always the slug itself, which the renderer joins
 * with the line's `api_prefix` to produce the actual API URL.
 */
export function buildLinePageConfig(lines: BusinessLine[]): LinePageMap {
  const out: LinePageMap = {};
  for (const line of lines) {
    const entry: Record<string, PageSpec> = {};
    // Always populate the line root (empty slug).
    entry[""] = {
      kind: inferPageKind(line, "", ""),
      title: line.display_name ?? line.name ?? line.id,
    };
    for (const nav of line.nav ?? []) {
      // Path is like "/valuation/reports" or "/retail-leasing/market-report".
      // Strip the leading "/" and the line-id prefix to derive the slug.
      // e.g. "/valuation/reports" → "reports"
      //      "/retail-leasing/market-report" → "market-report"
      //      "/my-line/ping" → "ping"
      const trimmed = nav.path.replace(/^\//, "");
      const slug = trimmed.startsWith(line.id + "/")
        ? trimmed.slice(line.id.length + 1)
        : trimmed === line.id
          ? ""
          : trimmed;
      entry[slug] = {
        kind: inferPageKind(line, slug, nav.title),
        detailEndpoint: slug === "" ? undefined : slug,
        title: nav.title,
      };
    }
    out[line.id] = entry;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Runtime spec table — the live cache used by the renderers.
// ---------------------------------------------------------------------------
//
// Initialized empty so the synchronous `getPageSpec()` API still works
// during the first paint (returns "not-integrated" gracefully until the
// registry is loaded). Populated by the [line]/[page] page component
// via `setLinePageConfig()`. This is the same pattern as `setApiPrefix`
// in [line]/[page]/page.tsx — purely a runtime cache for the spec
// table.
// ---------------------------------------------------------------------------

let LIVE_LINE_PAGE_SPECS: LinePageMap = {};

/** Replace the runtime spec table. Called by the [line]/[page] page after
 * the registry fetch resolves. */
export function setLinePageConfig(map: LinePageMap): void {
  LIVE_LINE_PAGE_SPECS = map;
}

/** Read the current spec table. Mostly for tests. */
export function getLinePageConfig(): LinePageMap {
  return LIVE_LINE_PAGE_SPECS;
}

/** Look up a PageSpec for a (line, page) pair. Falls back to
 * "not-integrated" if the spec table has not been populated yet
 * (e.g. before the registry fetch resolves) or if the (line, page)
 * combination is unknown. */
export function getPageSpec(line: string, page: string | undefined): PageSpec {
  const lineEntry = LIVE_LINE_PAGE_SPECS[line];
  if (!lineEntry) {
    // Defensive: before the registry fetch resolves, the table is empty.
    // We still want the line root to be a "line-overview" so the page
    // shell renders; everything else is "not-integrated" until the
    // table is populated.
    if (!page) return { kind: "line-overview" };
    return { kind: "not-integrated" };
  }
  const slug = page ?? "";
  const spec = lineEntry[slug];
  if (spec) return spec;
  // Fallback: line root or empty slug
  if (slug === "") return { kind: "line-overview" };
  return { kind: "not-integrated" };
}

/** True if (line, page) is recognized as a valid nav entry, false otherwise. */
export function isKnownLine(line: string): boolean {
  if (Object.prototype.hasOwnProperty.call(LIVE_LINE_PAGE_SPECS, line)) {
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Backwards-compat exports.
// ---------------------------------------------------------------------------
//
// The original `LINE_PAGE_SPECS` constant is preserved (empty) so any
// legacy code that imports it compiles. The runtime is now driven by
// the dynamic table above.
// ---------------------------------------------------------------------------

/** @deprecated Use the dynamic builder (`buildLinePageConfig`) + the
 * runtime cache (`setLinePageConfig` / `getPageSpec`). The empty
 * default makes legacy `LINE_PAGE_SPECS[line]` reads return undefined
 * — callers must go through `getPageSpec` instead. */
export const LINE_PAGE_SPECS: LinePageMap = {};
