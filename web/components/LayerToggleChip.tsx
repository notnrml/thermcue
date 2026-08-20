"use client";

interface LayerToggleChipProps {
  label: string;
  on: boolean;
  onToggle: () => void;
}

export default function LayerToggleChip({
  label,
  on,
  onToggle,
}: LayerToggleChipProps) {
  return (
    <button
      onClick={onToggle}
      aria-pressed={on}
      className={`inline-flex items-center gap-1.5 rounded-pill border px-3 py-1 text-caption font-medium transition-colors duration-200 ease-out ${
        on
          ? "border-base-tertiary bg-base-elevated text-base-text"
          : "border-base-border bg-base-surface/80 text-base-muted hover:text-base-secondary"
      }`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-pill ${
          on ? "bg-base-text" : "bg-base-border"
        }`}
        aria-hidden
      />
      {label}
    </button>
  );
}
