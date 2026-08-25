/* =============================================================================
 * PLAN WORKSPACE SKELETON.
 *
 * The engine runs a full thermal pipeline and an optimiser search on a cold
 * request, which takes tens of seconds. A spinner for that long reads as a hang;
 * a blank screen reads as a broken deployment. This skeleton mirrors the real
 * layout, so the shape of the product is legible before any data arrives and the
 * eventual paint is a fill rather than a jump.
 *
 * Deliberately still: no shimmer sweep. The brief asks for calm operational
 * authority, and an animated gradient crawling across a control room is
 * decoration, not information. Motion here is one slow opacity breath, and it is
 * suppressed under prefers-reduced-motion by the utility in globals.css.
 * ========================================================================== */

function Block({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-card bg-base-elevated motion-reduce:animate-none ${className}`}
      aria-hidden
    />
  );
}

export default function PlanLoading() {
  return (
    <div
      className="flex h-screen flex-col"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="sr-only">
        Loading the Phoenix plan. The engine is pulling FortyGuard data and
        searching for an operating plan.
      </span>

      <div className="flex min-h-0 flex-1">
        {/* Map canvas, roughly 60% */}
        <div className="relative min-w-0 flex-[3] border-r border-base-border bg-base-surface p-6">
          <div className="flex gap-2">
            <Block className="h-7 w-28" />
            <Block className="h-7 w-24" />
            <Block className="h-7 w-20" />
            <Block className="h-7 w-24" />
            <Block className="ml-auto h-7 w-44" />
          </div>
          <Block className="mt-6 h-[calc(100%-9rem)] w-full" />
          <div className="absolute inset-x-6 bottom-6">
            <Block className="h-14 w-full" />
          </div>
        </div>

        {/* Right rail, roughly 40% */}
        <div className="flex min-w-0 flex-[2] flex-col gap-6 bg-base-bg p-6">
          <div className="flex gap-2">
            <Block className="h-8 w-24" />
            <Block className="h-8 w-20" />
            <Block className="h-8 w-24" />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Block className="h-24" />
            <Block className="h-24" />
            <Block className="h-24" />
            <Block className="h-24" />
          </div>

          <Block className="h-48 w-full" />

          <div className="space-y-3">
            <Block className="h-12 w-full" />
            <Block className="h-12 w-full" />
            <Block className="h-12 w-full" />
          </div>
        </div>
      </div>

      <div className="border-t border-base-border bg-base-surface px-6 py-3">
        <p className="text-caption text-base-secondary">
          Pulling FortyGuard hyperlocal temperature, computing shade from
          building geometry, and searching for an operating plan. The first load
          runs the full pipeline.
        </p>
      </div>
    </div>
  );
}
