/* =============================================================================
 * ENGINE CLIENT.
 *
 * The single place the frontend talks to the ThermCue engine. Components stay
 * unaware of it: they consume the typed shapes in web/types, and this module is
 * what turns an HTTP response into those shapes.
 *
 * The engine serves GET /plan as one complete PlanWorkspaceData payload
 * precisely so the first paint is not assembled from six round trips that can
 * disagree with each other about data freshness.
 * ========================================================================== */

import type { PlanWorkspaceData } from "@/types";

/** Provenance the engine attaches to every plan payload. */
export interface EngineMeta {
  freshness: "live" | "cached";
  /** False when no FortyGuard key is configured, or every pull failed. */
  hasFortyguardSpatialSignal: boolean;
  /** Which source supplied which field, e.g. humidity: "fortyguard:/v1/env_params". */
  sources: Record<string, string>;
  /** Plain-language provenance and limitation notes, rendered in the UI. */
  notes: string[];
  /** Headline figures are reproducible from this seed. */
  seed: number;
}

export interface PlanPayload extends PlanWorkspaceData {
  meta: EngineMeta;
}

/** How the workspace got its data. Drives what the provenance strip says. */
export type PlanSource = "engine" | "fallback";

export interface PlanResult {
  data: PlanWorkspaceData;
  meta: EngineMeta | null;
  source: PlanSource;
  /** Populated only when source is "fallback". Rendered, never swallowed. */
  error: string | null;
}

export const ENGINE_URL =
  process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8000";

/* A cold engine runs the whole thermal pipeline plus an optimiser search on the
 * first request, which is tens of seconds. Anything shorter than this times out
 * exactly when a judge opens the link for the first time, which is the one
 * moment it must not. */
const PLAN_TIMEOUT_MS = 90_000;

async function getJson<T>(path: string, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${ENGINE_URL}${path}`, {
      signal: controller.signal,
      /* The plan depends on a live forecast and a live optimiser run, so it is
       * never statically cached at build time. Next would otherwise bake the
       * first response into the deployment and serve it for the whole event. */
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`${path} responded ${response.status} ${response.statusText}`);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Fetch the complete Plan Workspace payload.
 *
 * Never throws. A dead engine falls back to the bundled demo scenario and says
 * so, both in the returned `source` and in the banner the page renders, because
 * a judge opening the link during a deploy should see the product with a
 * truthful label rather than a stack trace. What it must never do is show
 * fallback data as though it came from the engine.
 */
export async function fetchPlan(fallback: PlanWorkspaceData): Promise<PlanResult> {
  try {
    const payload = await getJson<PlanPayload>("/plan", PLAN_TIMEOUT_MS);
    const { meta, ...data } = payload;
    return { data: data as PlanWorkspaceData, meta: meta ?? null, source: "engine", error: null };
  } catch (cause) {
    const message =
      cause instanceof Error
        ? cause.name === "AbortError"
          ? `Engine did not respond within ${PLAN_TIMEOUT_MS / 1000}s`
          : cause.message
        : String(cause);
    return { data: fallback, meta: null, source: "fallback", error: message };
  }
}

/** Health, used by the provenance strip to say which sources are configured. */
export interface EngineHealth {
  status: string;
  version: string;
  fortyguard_key_configured: boolean;
  anthropic_key_configured: boolean;
  offline_mode: boolean;
}

export async function fetchHealth(): Promise<EngineHealth | null> {
  try {
    return await getJson<EngineHealth>("/health", 8_000);
  } catch {
    return null;
  }
}

/**
 * Fire the demo trigger: perturb one zone's forecast and let the agent react.
 *
 * Returns the directive synchronously, because the acceptance criterion is a
 * traced autonomous replan inside 30 seconds and that is only demonstrable if
 * the response carries the directive rather than a job id.
 */
export interface AgentDirectivePayload {
  id: string;
  timestamp: string;
  type: "monitor" | "replan" | "directive" | "no-action";
  text: string;
  toolTrace: { tool: string; input: string; output: string }[];
  tag: string;
  engine: string;
  promptVersion: string;
  grounded: boolean;
  rejectedNumbers: number[];
}

export async function triggerAgent(
  zoneId: string,
  deltaC = 3,
): Promise<AgentDirectivePayload> {
  const response = await fetch(
    `${ENGINE_URL}/agent/trigger?zone_id=${encodeURIComponent(zoneId)}&delta_c=${deltaC}`,
    { method: "POST", cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(`Agent trigger failed: ${response.status}`);
  }
  return (await response.json()) as AgentDirectivePayload;
}

/** WebSocket URL for the live agent console. */
export function agentSocketUrl(): string {
  return `${ENGINE_URL.replace(/^http/, "ws")}/agent`;
}

/** Absolute URLs for the export endpoints, used by the export bar. */
export const exportUrls = {
  pdf: `${ENGINE_URL}/export/pdf`,
  ics: `${ENGINE_URL}/export/ics`,
};
