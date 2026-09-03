// packages/ui/src/index.ts
// Public surface of the universal UI component library.

export { UniversalKpiCard } from "./UniversalKpiCard";
export type {
  UniversalKpiCardProps,
  UniversalKpiIndicator,
} from "./UniversalKpiCard";

export { UniversalChart } from "./UniversalChart";
export type {
  UniversalChartProps,
  UniversalChartType,
  UniversalChartDataPoint,
  UniversalChartSeriesData,
  UniversalChartOptions,
} from "./UniversalChart";

export { UniversalAgGrid } from "./UniversalAgGrid";
export type { UniversalAgGridProps } from "./UniversalAgGrid";

export { EmptyState } from "./EmptyState";
export type { EmptyStateProps } from "./EmptyState";

export { RoleSwitcher, DEFAULT_ROLES } from "./RoleSwitcher";
export type { RoleSwitcherProps, RoleName } from "./RoleSwitcher";
