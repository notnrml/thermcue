import type { DataFreshness } from "@/types";

interface LiveCachedBadgeProps {
  freshness: DataFreshness;
}

export default function LiveCachedBadge({ freshness }: LiveCachedBadgeProps) {
  const live = freshness === "live";
  return (
    <span className="inline-flex items-center gap-2 rounded-pill border border-base-border bg-base-surface/90 px-3 py-1 text-caption text-base-secondary">
      <span
        className={`h-2 w-2 rounded-pill ${live ? "bg-success" : "bg-warning"}`}
        aria-hidden
      />
      {live ? "Live FortyGuard data" : "Cached"}
    </span>
  );
}
