"use client";

import { useState } from "react";
import type {
  AgentFeedEntry as AgentFeedEntryData,
  KpiComparison,
  KpiSet,
  ParetoPoint,
  PlanChange,
  ValidationPoint,
  ValidationSummary,
  Zone,
} from "@/types";
import TabSet from "@/components/TabSet";
import KpiCard from "@/components/KpiCard";
import Button from "@/components/Button";
import ChangeListRow from "@/components/ChangeListRow";
import AgentFeedEntry from "@/components/AgentFeedEntry";
import ParetoChart from "@/components/charts/ParetoChart";
import ValidationChart from "@/components/charts/ValidationChart";

interface RightRailProps {
  kpis: KpiComparison;
  paretoPoints: ParetoPoint[];
  planChanges: PlanChange[];
  expandedChangeId: string | null;
  onToggleChange: (id: string) => void;
  agentFeed: AgentFeedEntryData[];
  replanActiveId: string | null;
  onSimulateForecast: () => void;
  simulating: boolean;
  validationPoints: ValidationPoint[];
  validationSummary: ValidationSummary;
  zones: Zone[];
  timezone: string;
}

const METRICS: {
  key: keyof KpiSet;
  label: string;
  unit?: string;
  /** True when a lower optimised value is an improvement. */
  lowerIsBetter: boolean;
}[] = [
  {
    key: "heatWeightedPersonMinutes",
    label: "Heat-weighted person-minutes",
    lowerIsBetter: true,
  },
  {
    key: "personMinutesHighExtreme",
    label: "Person-minutes in High and Extreme",
    lowerIsBetter: true,
  },
  { key: "totalWaitMinutes", label: "Total wait", unit: "min", lowerIsBetter: true },
  {
    key: "longestWaitMinutes",
    label: "Longest wait",
    unit: "min",
    lowerIsBetter: true,
  },
];

function formatValue(n: number): string {
  return Math.round(n).toLocaleString("en-GB");
}

/**
 * Percentage change from baseline to optimised.
 *
 * A zero baseline is a real case, not a defensive hypothetical: on a mild
 * forecast no zone reaches the High or Extreme band, so person-minutes in
 * High+Extreme is legitimately zero on both sides. Dividing by it rendered
 * "NaN%" in a pill on the primary KPI panel. There is no percentage change
 * between nothing and nothing, so the card shows no delta chip at all rather
 * than a number that cannot exist.
 */
function deltaFor(baseline: number, optimised: number) {
  if (baseline === 0) {
    return { text: optimised === 0 ? null : "new", pct: optimised === 0 ? 0 : 100 };
  }
  const pct = ((optimised - baseline) / baseline) * 100;
  const sign = pct > 0 ? "+" : "";
  return { text: `${sign}${pct.toFixed(0)}%`, pct };
}

export default function RightRail(props: RightRailProps) {
  const [tab, setTab] = useState("compare");

  return (
    <div className="flex h-full flex-col border-l border-base-border bg-base-surface">
      <div className="px-4 pt-2">
        <TabSet
          tabs={[
            { id: "compare", label: "Compare" },
            { id: "agent", label: "Agent" },
            { id: "validation", label: "Validation" },
          ]}
          value={tab}
          onChange={setTab}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === "compare" ? <CompareTab {...props} /> : null}
        {tab === "agent" ? <AgentTab {...props} /> : null}
        {tab === "validation" ? <ValidationTab {...props} /> : null}
      </div>
    </div>
  );
}

function CompareTab({
  kpis,
  paretoPoints,
  planChanges,
  expandedChangeId,
  onToggleChange,
}: RightRailProps) {
  return (
    <div className="space-y-5">
      <section>
        <div className="mb-2 grid grid-cols-2 gap-2">
          <span className="text-label uppercase tracking-wide text-base-muted">
            Baseline
          </span>
          <span className="text-label uppercase tracking-wide text-base-muted">
            Optimised
          </span>
        </div>
        <div className="space-y-2">
          {METRICS.map((m) => {
            const baseline = kpis.baseline[m.key];
            const optimised = kpis.optimised[m.key];
            const delta = deltaFor(baseline, optimised);
            const improved = m.lowerIsBetter ? delta.pct < 0 : delta.pct > 0;
            return (
              <div key={m.key} className="grid grid-cols-2 gap-2">
                <KpiCard
                  label={m.label}
                  value={formatValue(baseline)}
                  unit={m.unit}
                />
                <KpiCard
                  label={m.label}
                  value={formatValue(optimised)}
                  unit={m.unit}
                  delta={delta.text ?? undefined}
                  variant={improved ? "delta-positive" : "delta-negative"}
                />
              </div>
            );
          })}
        </div>
      </section>

      <section>
        <h3 className="mb-1 text-body font-semibold text-base-text">
          Pareto frontier
        </h3>
        <p className="mb-2 text-caption text-base-muted">
          Total wait against heat-weighted exposure. The chosen plan is marked
          in green; the baseline in light grey.
        </p>
        <div className="rounded-card border border-base-border bg-base-bg p-2">
          <ParetoChart points={paretoPoints} />
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-body font-semibold text-base-text">
          Plan changes
        </h3>
        <div className="space-y-2">
          {planChanges.map((change) => (
            <ChangeListRow
              key={change.id}
              change={change}
              expanded={expandedChangeId === change.id}
              onToggle={() => onToggleChange(change.id)}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function AgentTab({
  agentFeed,
  replanActiveId,
  onSimulateForecast,
  simulating,
  timezone,
}: RightRailProps) {
  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-caption text-base-muted">
          Autonomous agent console. Agent output is violet; user actions are
          not.
        </p>
        <Button
          size="sm"
          onClick={onSimulateForecast}
          disabled={simulating}
        >
          Simulate forecast update
        </Button>
      </div>
      <div className="space-y-2">
        {agentFeed.map((entry) => (
          <AgentFeedEntry
            key={entry.id}
            entry={entry}
            active={entry.id === replanActiveId}
            timezone={timezone}
          />
        ))}
      </div>
    </div>
  );
}

function ValidationTab({
  validationPoints,
  validationSummary,
  zones,
}: RightRailProps) {
  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-1 text-body font-semibold text-base-text">
          Zone temperatures against the airport station
        </h3>
        <p className="mb-2 text-caption text-base-muted">
          Per-zone FortyGuard readings across the day, with the single
          Phoenix Sky Harbor station value dashed.
        </p>
        <div className="rounded-card border border-base-border bg-base-bg p-2">
          <ValidationChart points={validationPoints} zones={zones} />
        </div>
      </section>

      <section className="rounded-card border border-base-border bg-base-elevated p-4">
        <span className="text-label uppercase tracking-wide text-base-muted">
          Max intra-venue spread
        </span>
        <p className="font-mono text-display text-base-text">
          {validationSummary.maxIntraVenueSpreadC.toFixed(1)} C
        </p>
      </section>

      <section className="rounded-card border border-wbgt-high/40 bg-wbgt-high/10 p-4">
        <p className="text-body font-semibold text-base-text">
          Plan built on station data alone differs:
        </p>
        <p className="mt-1 text-body text-base-secondary">
          {validationSummary.verdictDecision}
        </p>
      </section>
    </div>
  );
}
