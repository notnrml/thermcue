"use client";

import { useState } from "react";
import type {
  AgentFeedEntry as AgentFeedEntryData,
  AgentFeedType,
} from "@/types";

interface AgentFeedEntryProps {
  entry: AgentFeedEntryData;
  /** Shows the working spinner on replanning entries. */
  active?: boolean;
  /** IANA timezone for display; defaults to the viewer's local time. */
  timezone?: string;
}

const tagLabel: Record<AgentFeedType, string> = {
  monitor: "MONITOR",
  replan: "REPLAN",
  directive: "DIRECTIVE",
  "no-action": "MONITOR",
};

function formatTime(iso: string, timezone?: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: timezone,
  }).format(new Date(iso));
}

export default function AgentFeedEntry({
  entry,
  active = false,
  timezone,
}: AgentFeedEntryProps) {
  const [traceOpen, setTraceOpen] = useState(false);
  const isDirective = entry.type === "directive";
  const isQuiet = entry.type === "monitor" || entry.type === "no-action";

  return (
    <div
      className={`animate-feed-in rounded-card border p-3 ${
        isDirective
          ? "border-agent/50 bg-agent/10"
          : "border-base-border bg-base-surface"
      } ${isQuiet ? "opacity-80" : ""}`}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="font-mono text-label text-base-muted">
          {formatTime(entry.timestamp, timezone)}
        </span>
        <span className="rounded-pill bg-agent/15 px-2 py-0.5 font-mono text-label font-semibold tracking-wide text-agent">
          {tagLabel[entry.type]}
        </span>
        {entry.type === "replan" && active ? (
          <span
            className="h-3 w-3 animate-spin rounded-pill border border-agent border-t-transparent"
            role="status"
            aria-label="Replanning"
          />
        ) : null}
      </div>
      <p
        className={`text-body ${
          isDirective ? "text-base-text" : "text-base-secondary"
        }`}
      >
        {entry.text}
      </p>
      {entry.toolTrace.length > 0 ? (
        <div className="mt-2">
          <button
            onClick={() => setTraceOpen((v) => !v)}
            aria-expanded={traceOpen}
            className="trace-affordance text-caption text-base-muted"
          >
            {traceOpen ? "Hide tool trace" : "Show tool trace"}
          </button>
          {traceOpen ? (
            <div className="mt-2 space-y-1.5 rounded-input border border-base-border bg-base-bg p-2">
              {entry.toolTrace.map((t, i) => (
                <div key={i} className="font-mono text-label">
                  <span className="text-agent">{t.tool}</span>
                  <span className="text-base-muted">({t.input})</span>
                  <span className="text-base-secondary"> {"->"} {t.output}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
