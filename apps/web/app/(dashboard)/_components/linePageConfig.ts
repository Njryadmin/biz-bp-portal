// apps/web/app/(dashboard)/_components/linePageConfig.ts
//
// Page-spec table for the dynamic business-line router
// (apps/web/app/(dashboard)/[line]/[page]/page.tsx).
//
// This file is the ONLY place in the web app that knows which
// (line, page-slug) maps to which API endpoint + render kind. Adding a
// new business line or a new page here automatically wires it into the
// App Router — no new file needed under apps/web/app/(dashboard)/.
//
// Render kinds:
//   - "line-overview"     : /[line]              → fetch /indicators, render KPI grid
//   - "property-detail"   : /[line]/[page]       → fetch /properties, pick first id,
//                                                 then GET /properties/{id}/{detailEndpoint}
//   - "project-detail"    : /[line]/[page]       → fetch /projects, pick first id,
//                                                 then GET /projects/{id}/{detailEndpoint}
//   - "market-benchmark"  : /[line]/[page]       → fetch /market-benchmark, render bar + table
//   - "vacancy-alerts"    : /[line]/[page]       → fetch /vacancy-alerts, render summary + table
//   - "properties-summary": /[line]/[page]       → fetch /properties, render headline KPIs
//   - "ping-only"         : /[line] (my-line)    → fetch /ping, show "OK" status
//   - "not-integrated"    : fallback             → render friendly "未接入" message

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
}

type LinePageMap = Record<string, Record<string, PageSpec>>;

/**
 * Per (line, page) spec table. Keys are business-line ids; values map
 * the URL slug (e.g. "dynamic-pl") to a PageSpec.
 *
 * If a (line, page) is not in the table, the renderer shows a friendly
 * "未接入" message — NOT a 404. This honors the task's constraint of
 * not breaking the sidebar even for pages that don't yet have an
 * endpoint integration.
 */
export const LINE_PAGE_SPECS: LinePageMap = {
  residential: {
    // /residential (no page) uses line-overview; we treat the index entry
    // explicitly so lookups are predictable.
    "": { kind: "line-overview" },
    "dynamic-pl": { kind: "project-detail", detailEndpoint: "dynamic-pl" },
    payment: { kind: "project-detail", detailEndpoint: "payment" },
    redlines: { kind: "project-detail", detailEndpoint: "redlines" },
    "dedup-forecast": { kind: "project-detail", detailEndpoint: "dedup-forecast" },
  },
  retail: {
    "": { kind: "line-overview" },
    noi: { kind: "property-detail", detailEndpoint: "noi-waterfall" },
    efficiency: { kind: "not-integrated" }, // no API endpoint yet
    "brand-mix": { kind: "property-detail", detailEndpoint: "brand-mix" },
    "renovation-npv": { kind: "property-detail", detailEndpoint: "renovation-npv" },
    collection: { kind: "property-detail", detailEndpoint: "collection-rate" },
  },
  "retail-leasing": {
    "": { kind: "line-overview" },
    "market-report": { kind: "market-benchmark" },
    "vacancy-alert": { kind: "vacancy-alerts" },
    "leasing-kpi": { kind: "properties-summary" },
  },
  "my-line": {
    "": { kind: "ping-only" },
    ping: { kind: "not-integrated" }, // /ping is a health probe, not a UI page
  },
};

/** Look up a PageSpec for a (line, page) pair. */
export function getPageSpec(line: string, page: string | undefined): PageSpec {
  const lineEntry = LINE_PAGE_SPECS[line];
  if (!lineEntry) return { kind: "not-integrated" };
  const slug = page ?? "";
  const spec = lineEntry[slug];
  if (spec) return spec;
  // Fallback: try the line's "first nav" — if it has no nav entries
  // (e.g. my-line only has /ping), still show the overview.
  if (slug === "") return { kind: "line-overview" };
  return { kind: "not-integrated" };
}

/** True if (line, page) is recognized as a valid nav entry, false otherwise. */
export function isKnownLine(line: string): boolean {
  return Object.prototype.hasOwnProperty.call(LINE_PAGE_SPECS, line);
}
