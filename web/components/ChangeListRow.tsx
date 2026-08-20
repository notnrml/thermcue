"use client";

import type { PlanChange, PlanChangeKind } from "@/types";

interface ChangeListRowProps {
  change: PlanChange;
  expanded: boolean;
  onToggle: () => void;
}

const kindGlyph: Record<PlanChangeKind, string> = {
  gate: "G",
  staff: "S",
  water: "W",
  rest: "R",
};

const kindTitle: Record<PlanChangeKind, string> = {
  gate: "Gate change",
  staff: "Staffing change",
  water: "Water point",
  rest: "Rest point",
};

export default function ChangeListRow({
  change,
  expanded,
  onToggle,
}: ChangeListRowProps) {
  return (
    <div className="rounded-card border border-base-border bg-base-surface">
      <button
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-3 p-3 text-left"
      >
        <span
          title={kindTitle[change.kind]}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-input border border-base-border bg-base-elevated font-mono text-data-mono text-base-tertiary"
        >
          {kindGlyph[change.kind]}
        </span>
        <span className="flex-1 text-body text-base-text">{change.action}</span>
        <span className="flex shrink-0 gap-1">
          {change.timeChips.map((chip) => (
            <span
              key={chip}
              className="rounded-pill bg-base-elevated px-2 py-0.5 font-mono text-label text-base-secondary"
            >
              {chip}
            </span>
          ))}
        </span>
        <span
          className={`text-caption text-base-muted transition-transform duration-200 ease-out ${
            expanded ? "rotate-180" : ""
          }`}
          aria-hidden
        >
          v
        </span>
      </button>

      {expanded ? (
        <div className="border-t border-base-border p-3">
          <p className="mb-2 text-label uppercase tracking-wide text-base-muted">
            Why
          </p>
          <ol className="mb-3 space-y-1.5">
            {change.whyTrace.map((step, i) => (
              <li key={step.stage} className="flex items-start gap-2">
                <span className="mt-0.5 font-mono text-label text-base-muted">
                  {i + 1}
                </span>
                <span className="text-caption text-base-secondary">
                  <span className="font-medium text-base-tertiary">
                    {step.stage}:
                  </span>{" "}
                  {step.detail}
                </span>
              </li>
            ))}
          </ol>
          <div>
            <div className="mb-1 flex items-baseline justify-between">
              <span className="text-label text-base-muted">
                This change alone
              </span>
              <span className="font-mono text-data-mono text-base-text">
                {change.counterfactualPercent}% of the improvement
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-pill bg-base-elevated">
              <div
                className="h-full rounded-pill bg-success transition-[width] duration-200 ease-out"
                style={{ width: `${change.counterfactualPercent}%` }}
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
