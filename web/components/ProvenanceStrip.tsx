"use client";

/* =============================================================================
 * PROVENANCE STRIP.
 *
 * The design brief says honesty is a design feature and asks for it to be
 * elegant rather than apologetic. This is that surface: one quiet line stating
 * where the data came from, expanding on demand into the engine's full
 * provenance and limitation notes.
 *
 * It is deliberately subordinate to the map. The map is the focal point of this
 * screen; a caveat bar that competes with it would be a second focal point, and
 * two focal points read as a dashboard.
 *
 * Contrast against base-surface #121A2B:
 *   base-secondary #94A3B8  6.78:1  pass AA for body
 *   base-muted     #64748B  3.65:1  large text only, so it is used for the
 *                                   rule and the chevron, never for prose.
 * ========================================================================== */

import { useState } from "react";
import type { EngineMeta, PlanSource } from "@/lib/engine";

interface ProvenanceStripProps {
  meta: EngineMeta | null;
  source: PlanSource;
  error: string | null;
}

export default function ProvenanceStrip({
  meta,
  source,
  error,
}: ProvenanceStripProps) {
  const [open, setOpen] = useState(false);

  const notes = meta?.notes ?? [];
  const sources = meta?.sources ?? {};
  const degraded = source === "fallback" || meta?.hasFortyguardSpatialSignal === false;

  const summary =
    source === "fallback"
      ? "Showing the bundled demo scenario: the engine is not reachable"
      : meta?.hasFortyguardSpatialSignal
        ? "Live engine, FortyGuard hyperlocal signal applied"
        : "Live engine, no FortyGuard spatial signal applied";

  return (
    <section
      className="border-t border-base-border bg-base-surface"
      aria-label="Data provenance"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-6 py-3 text-left transition-colors duration-150 ease-out hover:bg-base-elevated"
      >
        <span
          className={`h-2 w-2 shrink-0 rounded-pill ${
            degraded ? "bg-warning" : "bg-success"
          }`}
          aria-hidden
        />
        <span className="text-caption text-base-secondary">{summary}</span>

        {meta ? (
          <span className="font-mono text-label text-base-secondary">
            seed {meta.seed}
          </span>
        ) : null}

        <span className="ml-auto flex items-center gap-2 text-label text-base-secondary">
          {notes.length > 0 ? `${notes.length} notes` : "Details"}
          <svg
            width="10"
            height="10"
            viewBox="0 0 10 10"
            aria-hidden
            className={`transition-transform duration-150 ease-out ${
              open ? "rotate-180" : ""
            }`}
          >
            <path
              d="M1 3l4 4 4-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </button>

      {open ? (
        <div className="space-y-4 border-t border-base-border px-6 py-4">
          {error ? (
            <p className="text-caption text-base-tertiary">
              <span className="text-base-secondary">Engine unreachable.</span>{" "}
              {error}. Every figure below comes from the bundled demo scenario
              and is illustrative, not measured.
            </p>
          ) : null}

          {Object.keys(sources).length > 0 ? (
            <dl className="grid grid-cols-2 gap-x-8 gap-y-2">
              {Object.entries(sources).map(([field, origin]) => (
                <div key={field} className="flex gap-3">
                  <dt className="w-44 shrink-0 text-label text-base-secondary">
                    {field.replace(/_/g, " ")}
                  </dt>
                  <dd className="font-mono text-label text-base-tertiary">
                    {origin}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}

          {notes.length > 0 ? (
            <ul className="space-y-2">
              {notes.map((note) => (
                <li
                  key={note}
                  className="border-l border-base-border pl-3 text-caption text-base-tertiary"
                >
                  {note}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
