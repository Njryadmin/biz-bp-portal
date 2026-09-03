// packages/ui/src/UniversalAgGrid.tsx
// Thin AG Grid Community wrapper. The grid is *not* rendered in this MVP build
// but the component is exported for T1+ to consume.
import { AgGridReact } from "ag-grid-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import type { ColDef, GridReadyEvent } from "ag-grid-community";
import type { CSSProperties } from "react";

export interface UniversalAgGridProps<T = Record<string, unknown>> {
  rowData: T[];
  columnDefs: ColDef<T>[];
  style?: CSSProperties;
  onGridReady?: (e: GridReadyEvent<T>) => void;
  pagination?: boolean;
  paginationPageSize?: number;
}

export function UniversalAgGrid<T = Record<string, unknown>>({
  rowData,
  columnDefs,
  style,
  onGridReady,
  pagination = true,
  paginationPageSize = 20,
}: UniversalAgGridProps<T>) {
  return (
    <div className="ag-theme-quartz" style={{ width: "100%", ...style }}>
      <AgGridReact<T>
        rowData={rowData}
        columnDefs={columnDefs}
        onGridReady={onGridReady}
        pagination={pagination}
        paginationPageSize={paginationPageSize}
      />
    </div>
  );
}
