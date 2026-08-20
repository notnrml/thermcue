type KpiVariant = "default" | "delta-positive" | "delta-negative";

interface KpiCardProps {
  label: string;
  value: string;
  unit?: string;
  variant?: KpiVariant;
  /** Delta chip text, e.g. "-38%". Shown when provided. */
  delta?: string;
  /** Adds the dotted-underline trace affordance to the value. */
  hasTrace?: boolean;
  onTraceClick?: () => void;
}

const deltaClasses: Record<KpiVariant, string> = {
  default: "bg-base-elevated text-base-secondary",
  "delta-positive": "bg-success/15 text-success",
  "delta-negative": "bg-wbgt-extreme/15 text-wbgt-extreme-text",
};

export default function KpiCard({
  label,
  value,
  unit,
  variant = "default",
  delta,
  hasTrace = false,
  onTraceClick,
}: KpiCardProps) {
  return (
    <div className="flex flex-col gap-1 rounded-card border border-base-border bg-base-surface p-3">
      <span className="text-label uppercase tracking-wide text-base-muted">
        {label}
      </span>
      <div className="flex items-baseline justify-between gap-2">
        <span
          className={`font-mono text-h2 text-base-text ${
            hasTrace ? "trace-affordance" : ""
          }`}
          onClick={hasTrace ? onTraceClick : undefined}
          role={hasTrace ? "button" : undefined}
        >
          {value}
          {unit ? (
            <span className="ml-1 font-mono text-data-mono text-base-secondary">
              {unit}
            </span>
          ) : null}
        </span>
        {delta ? (
          <span
            className={`rounded-pill px-2 py-0.5 font-mono text-label ${deltaClasses[variant]}`}
          >
            {delta}
          </span>
        ) : null}
      </div>
    </div>
  );
}
