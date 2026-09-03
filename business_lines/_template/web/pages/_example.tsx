// business_lines/_template/web/pages/_example.tsx
// Template page for a business line.
// Usage: copy to business_lines/<line>/web/pages/index.tsx and edit.
// This file is dynamically loaded by apps/web via the registry —
// do NOT import it from apps/web/.

import { UniversalKpiCard } from "@fin-bp/ui";
import { UniversalChart } from "@fin-bp/ui";
import { EmptyState } from "@fin-bp/ui";
import type { BusinessLine, Indicator, KpiValue } from "@fin-bp/types";

interface Props {
  line: BusinessLine;
  indicators: Indicator[];
}

export default function ExamplePage({ line, indicators }: Props) {
  return (
    <div style={{ padding: 24 }}>
      <h1>{line.name}</h1>
      <p>{line.description}</p>

      {indicators.length === 0 ? (
        <EmptyState
          title="No indicators defined"
          description="Add indicators in business_lines/<line>/indicators.yaml"
        />
      ) : (
        <>
          <h2>KPIs</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16 }}>
            {indicators.map((ind) => (
              <UniversalKpiCard
                key={ind.id}
                title={ind.title}
                value={null as unknown as KpiValue}
                format={ind.format as any}
                unit={ind.unit}
                loading
              />
            ))}
          </div>

          <h2 style={{ marginTop: 32 }}>Charts</h2>
          <UniversalChart
            option={{}}
            style={{ height: 320 }}
            loading
          />
        </>
      )}
    </div>
  );
}
