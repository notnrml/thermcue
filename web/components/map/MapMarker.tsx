"use client";

type MarkerKind = "gate" | "water" | "rest" | "staff";

interface MapMarkerProps {
  kind: MarkerKind;
  label: string;
  /** Gate markers grow a vertical queue bar; 0 to 1 of the max queue. */
  queueFraction?: number;
  /** Queue length shown beside the bar for gates. */
  queueLength?: number;
  movable?: boolean;
  dragging?: boolean;
}

const kindGlyph: Record<MarkerKind, string> = {
  gate: "G",
  water: "W",
  rest: "R",
  staff: "S",
};

const kindClasses: Record<MarkerKind, string> = {
  gate: "border-base-tertiary bg-base-elevated text-base-text",
  water: "border-wbgt-low bg-base-elevated text-wbgt-low",
  rest: "border-success bg-base-elevated text-success",
  staff: "border-agent bg-base-elevated text-agent",
};

const QUEUE_BAR_MAX_PX = 48;

export default function MapMarker({
  kind,
  label,
  queueFraction = 0,
  queueLength,
  movable = false,
  dragging = false,
}: MapMarkerProps) {
  return (
    <div
      className={`flex flex-col items-center ${movable ? "cursor-grab" : ""} ${
        dragging ? "cursor-grabbing opacity-80" : ""
      }`}
    >
      {kind === "gate" ? (
        <div className="mb-1 flex items-end gap-1">
          <div
            className="w-2 rounded-t-sm bg-wbgt-high transition-[height] duration-200 ease-out"
            style={{
              height: `${Math.max(2, Math.round(queueFraction * QUEUE_BAR_MAX_PX))}px`,
            }}
            title={
              queueLength !== undefined
                ? `${queueLength} people queueing`
                : undefined
            }
          />
          {queueLength !== undefined ? (
            <span className="font-mono text-label text-base-tertiary">
              {queueLength}
            </span>
          ) : null}
        </div>
      ) : null}
      <div
        className={`flex h-7 w-7 items-center justify-center rounded-pill border-2 font-mono text-data-mono font-semibold shadow-md ${kindClasses[kind]}`}
      >
        {kindGlyph[kind]}
      </div>
      <span className="mt-0.5 rounded-input border border-base-border bg-base-bg/90 px-1.5 py-0.5 text-label font-medium text-base-text shadow-sm">
        {label}
      </span>
    </div>
  );
}
