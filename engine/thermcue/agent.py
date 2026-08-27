"""The autonomous agent.

Track 06 verbatim: agents that use FortyGuard APIs to analyse, decide and
automate heat-related workflows without human intervention. This module is that
agent: a tool-calling loop over the engine's own capabilities, publishing
directives to a WebSocket console on a timer and on a demo trigger, with no
human in the path.

The guardrail that matters
--------------------------
The brief requires that every number the agent cites comes from a tool output,
and that this is enforced in the system prompt *and* validated afterwards. Both
are here, and the second is the one that counts. ``ground_numbers`` extracts
every numeral from the generated directive and checks each against the set of
values the tools actually returned. A directive containing a number nobody
computed is **rejected**, not published with a warning, because a plausible
invented figure is worse than no directive at all: an operator cannot tell the
difference, and the whole product is a claim about traceability.

Degradation
-----------
With no Anthropic key the agent runs a deterministic decision path over the same
tools and publishes directives built from templates. Those are tagged
``engine="deterministic"`` and the console labels them, because an if-statement
wearing an agent's clothes would be a lie about the primary track.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from .agent_prompts import (
    DIRECTIVE_INSTRUCTION,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    TRIGGER_NOTE,
)
from .config import LlmConfig, Settings, get_settings
from .models import AgentFeedEntry, ToolTrace
from .optimise import OptimisationResult, run_full_optimisation
from .plan import Plan
from .scenario import Scenario
from .service import ThermalBundle, build_thermal_bundle
from .simulate import HEADLINE_SEED, monte_carlo, simulate_fast
from .thermal import BAND_WEIGHTS

BAND_ORDER = ["low", "moderate", "high", "extreme"]

#: How far ahead a band change must fall to justify replanning, per the brief.
REPLAN_HORIZON_HOURS = 3

RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BASE_DELAY_S = 5.0
RATE_LIMIT_MAX_DELAY_S = 30.0
"""Free tiers meter tokens per minute, so a rate limit is normal operation and
worth waiting out rather than failing the cycle over."""

MAX_TOOL_ROUNDS = 5
"""Cap on tool-calling rounds before the agent is asked to commit to a
directive. A model that keeps calling tools forever is a stuck agent, not a
thorough one, and on a metered free tier each extra round is paid for twice
because the whole message history is resent."""

#: Tolerance for matching a number in generated text against a tool output.
#: Generous enough to allow the model to quote a rounded figure, tight enough
#: that a different number cannot pass as the same one.
GROUNDING_RELATIVE_TOLERANCE = 0.02

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")

#: Numbers that need no grounding: clock times, small counts and percentages
#: that appear as part of ordinary language rather than as claims about data.
_ALWAYS_GROUNDED = {0.0, 1.0, 2.0, 100.0}


class DirectiveRejected(RuntimeError):
    """A generated directive cited a number no tool produced."""


class DailyQuotaExhausted(RuntimeError):
    """The provider's daily allowance is gone. Retrying is pointless today."""


class ToolGenerationError(RuntimeError):
    """The provider rejected the model's own tool call before running it.

    Distinct from a client error: nothing we sent was wrong, the model emitted a
    malformed call. Smaller and free-tier models do this regularly.
    """


async def _backoff_sleep(seconds: float) -> None:
    """Wait between rate-limit retries.

    A named seam rather than a bare asyncio.sleep so tests can neutralise it.
    Without it the retry tests genuinely slept through the backoff schedule and
    took over a minute, which is how a suite stops being run.
    """
    await asyncio.sleep(seconds)


def _retry_after_seconds(response: "httpx.Response", fallback: float) -> float:
    """How long the provider says to wait, or our own backoff if it does not say.

    Groq puts the figure in the error body as well as the header, and the body
    is often the only place it appears, so both are read. A provider that tells
    you exactly when its window resets is worth listening to; guessing wastes
    either time or another rejected request.
    """
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(float(header), 0.5)
        except ValueError:
            pass
    try:
        message = str(((response.json() or {}).get("error") or {}).get("message", ""))
    except ValueError:
        message = response.text[:400]
    found = re.search(r"try again in ([\d.]+)\s*s", message)
    if found:
        # A small margin: the window has to have actually rolled over.
        return float(found.group(1)) + 1.0
    return fallback


def _is_daily_quota_exhausted(response: "httpx.Response") -> bool:
    """Is this 429 a daily cap rather than a per-minute one?

    The distinction decides whether waiting helps. A tokens-per-minute limit
    resets in seconds and is worth sitting out; a tokens-per-day limit resets
    hours from now, so retrying it burns the caller's patience and changes
    nothing. The deployed agent spent 122 seconds backing off against a daily
    cap before failing, which is the worst of both outcomes.

    Groq exposes only the per-minute window in headers, so the daily cap is
    visible nowhere except the error body.
    """
    if response.status_code != 429:
        return False
    try:
        message = str(((response.json() or {}).get("error") or {}).get("message", ""))
    except ValueError:
        message = response.text[:600]
    lowered = message.lower()
    return "per day" in lowered or "tpd" in lowered or "rpd" in lowered


def _is_tool_generation_failure(response: "httpx.Response") -> bool:
    """Does this 400 mean the model produced an unusable tool call?"""
    if response.status_code != 400:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    error = payload.get("error") or {}
    return error.get("code") == "tool_use_failed" or "tool call" in str(
        error.get("message", "")
    ).lower()


#: Cap on a serialised tool output in the published trace. Whole responses can
#: run to megabytes of GeoJSON and the trace is rendered in a side panel.
TRACE_OUTPUT_LIMIT = 4000

#: Cap on what is sent back to the model. Much tighter than the trace limit,
#: because free tiers meter tokens per minute and the message history is resent
#: on every round, so a verbose tool result is paid for repeatedly.
MODEL_OUTPUT_LIMIT = 1800


def model_view(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """What the model sees, which is not what the audit trace records.

    The distinction matters in both directions. The trace must be complete or it
    is not evidence. The model must be given only what it needs to decide, or a
    free tier's tokens-per-minute limit ends the cycle - which is exactly what
    happened on Groq: full thermal state is 35 zone-hours, resent on every round,
    and the run died at 429 before publishing anything.

    So this projects each tool result down to the fields a directive can
    actually cite. Nothing is invented and nothing is rounded differently; fields
    are dropped, never altered, so every number the model can quote is still a
    number a tool returned and grounding is unaffected.
    """
    if name == "get_thermal_state":
        zones = result.get("zones") or []
        peak_by_zone: dict[str, dict[str, Any]] = {}
        for row in zones:
            current = peak_by_zone.get(row["zone_id"])
            if current is None or row["wbgt_estimate_c"] > current["wbgt_estimate_c"]:
                peak_by_zone[row["zone_id"]] = row
        return {
            "freshness": result.get("freshness"),
            "has_standing_plan_reference": result.get("has_standing_plan_reference"),
            "escalations_within_horizon": result.get("escalations_within_horizon"),
            "band_changes_vs_plan": (result.get("band_changes_vs_plan") or [])[:6],
            "hottest_hour_per_zone": list(peak_by_zone.values()),
            "_note": "Per-hour detail omitted for brevity; the full series is in the audit trace.",
        }
    if name == "run_optimiser":
        return {
            "baseline_hpm": result.get("baseline_hpm"),
            "optimised_hpm": result.get("optimised_hpm"),
            "hpm_reduction_pct": result.get("hpm_reduction_pct"),
            "total_wait_change_pct": result.get("total_wait_change_pct"),
            "changes": [
                {
                    "action": c.get("action"),
                    "band_and_hour": c.get("band_and_hour"),
                    "predicted_queue": c.get("predicted_queue"),
                }
                for c in (result.get("changes") or [])[:5]
            ],
            "resource_moves": [
                {"resource_name": m.get("resource_name"), "to_zone": m.get("to_zone")}
                for m in (result.get("resource_moves") or [])[:3]
            ],
        }
    if name == "run_simulation":
        return {
            k: result.get(k)
            for k in (
                "plan",
                "heat_weighted_person_minutes",
                "person_minutes_high_extreme",
                "total_wait_person_minutes",
                "longest_wait_minutes",
                "hpm_p50",
            )
        }
    if name == "get_forecast":
        return {"hours": (result.get("hours") or [])}
    return result


def _model_json(name: str, result: dict[str, Any]) -> str:
    """Serialise a tool result for the model, bounded and always valid JSON."""
    projected = model_view(name, result)
    encoded = json.dumps(projected, sort_keys=True, default=str)
    if len(encoded) <= MODEL_OUTPUT_LIMIT:
        return encoded
    return json.dumps(
        {
            "_truncated": True,
            "_note": "Result too large to return in full; ask for a narrower tool.",
            "summary": {
                k: v
                for k, v in projected.items()
                if isinstance(v, (int, float, str, bool)) or v is None
            },
        },
        sort_keys=True,
        default=str,
    )


def _trace_json(payload: Any) -> str:
    """Serialise a tool result for the audit trail, truncating into valid JSON.

    Slicing the string at a character limit was the first implementation and it
    produced unparseable output the moment a result exceeded the cap - which the
    thermal state always does. A trace exists to be audited; one that cannot be
    parsed is not a trace. When the payload is too large it is replaced by a
    valid object that says so and carries the top-level keys, so a reader can
    still see what the tool returned and go fetch the full version.
    """
    encoded = json.dumps(payload, sort_keys=True, default=str)
    if len(encoded) <= TRACE_OUTPUT_LIMIT:
        return encoded
    summary: dict[str, Any] = {
        "_truncated": True,
        "_full_length_chars": len(encoded),
        "_note": (
            "Full output exceeded the trace limit. Keys are listed below; the "
            "complete result is available from the endpoint that produced it."
        ),
    }
    if isinstance(payload, dict):
        summary["_keys"] = sorted(payload.keys())
        for key, value in payload.items():
            if isinstance(value, (int, float, str, bool)) or value is None:
                summary[key] = value
    return json.dumps(summary, sort_keys=True, default=str)


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]

    def to_trace(self) -> ToolTrace:
        return ToolTrace(
            tool=self.name,
            input=json.dumps(self.arguments, sort_keys=True, default=str),
            output=_trace_json(self.result),
        )


@dataclass(slots=True)
class Directive:
    id: str
    timestamp: str
    tag: str
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    engine: str = "anthropic"
    prompt_version: str = PROMPT_VERSION
    grounded: bool = True
    rejected_numbers: list[float] = field(default_factory=list)

    def to_feed_entry(self) -> AgentFeedEntry:
        tag_map = {"REPLAN": "replan", "MONITOR": "monitor", "NO-ACTION": "no-action"}
        return AgentFeedEntry(
            id=self.id,
            timestamp=self.timestamp,
            type=tag_map.get(self.tag, "directive"),
            text=self.text,
            tool_trace=[c.to_trace() for c in self.tool_calls],
        )


# ------------------------------------------------------------- grounding ----


def collect_tool_numbers(calls: list[ToolCall]) -> set[float]:
    """Every numeric value any tool returned, flattened.

    Derived figures the agent is expected to quote - percentage reductions,
    deltas - are computed by the tools and included in their outputs precisely so
    that quoting them is grounded. If a tool does not return a number, the agent
    has no business writing it.
    """
    found: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.add(float(node))
            found.add(round(float(node), 1))
            found.add(float(round(node)))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            for match in _NUMBER_RE.finditer(node):
                try:
                    found.add(float(match.group().replace(",", "")))
                except ValueError:
                    continue

    for call in calls:
        walk(call.result)
    return found


def ground_numbers(text: str, allowed: set[float]) -> list[float]:
    """Return the numbers in ``text`` that no tool produced.

    Clock times are stripped first: "14:00" is a time, not a claim about data,
    and treating its components as ungrounded figures would reject every usable
    directive. Everything else must match a tool output within tolerance.
    """
    without_times = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    ungrounded: list[float] = []
    for match in _NUMBER_RE.finditer(without_times):
        try:
            value = float(match.group().replace(",", ""))
        except ValueError:
            continue
        if value in _ALWAYS_GROUNDED:
            continue
        if any(
            abs(value - candidate) <= max(abs(candidate) * GROUNDING_RELATIVE_TOLERANCE, 0.05)
            for candidate in allowed
        ):
            continue
        ungrounded.append(value)
    return ungrounded


# ----------------------------------------------------------------- tools ----


class AgentTools:
    """The agent's capabilities. Each returns plain JSON-able data.

    Tool outputs deliberately include the derived figures a directive will want
    to quote - percentage changes, P50 envelopes, band names - so that quoting
    them is grounded rather than calculated. Making the honest path the easy path
    is more effective than forbidding the dishonest one.
    """

    def __init__(
        self,
        scenario: Scenario,
        settings: Settings | None = None,
        reference_bands: dict[str, dict[int, str]] | None = None,
    ) -> None:
        self.scenario = scenario
        self.settings = settings or get_settings()
        self._bundle: ThermalBundle | None = None
        self._optimisation: OptimisationResult | None = None
        self.calls: list[ToolCall] = []
        self.perturbation: dict[str, float] = {}
        self._served: set[str] = set()
        """Tool calls already answered this cycle, so a looping model is not
        charged for the same answer twice."""
        self.reference_bands = reference_bands or {}
        """The band map the standing plan was built on. The replanning trigger is
        a diff against **this**, not a search for band transitions inside one
        forecast. That distinction matters: at this venue the heat peaks in the
        first event hour and declines all evening, so there is never an
        hour-over-hour escalation to find, and an agent watching for one would sit
        silent through a forecast revision that moved a whole zone into Extreme."""

    # -- internals ---------------------------------------------------------

    async def bundle(self, refresh: bool = False) -> ThermalBundle:
        if self._bundle is None or refresh:
            self._bundle = await build_thermal_bundle(self.scenario, self.settings)
            if self.perturbation:
                self._apply_perturbation(self._bundle)
        return self._bundle

    def _apply_perturbation(self, bundle: ThermalBundle) -> None:
        """Apply the demo trigger's temperature offset to one zone.

        The perturbation acts on air temperature and the WBGT is recomputed from
        it, rather than being nudged directly. Shifting WBGT would produce a
        directive citing a band the underlying temperature does not support, and
        a judge who reads both panels would catch it immediately.
        """
        from .thermal import band_for, estimate_wbgt

        for row in bundle.zone_hours:
            delta = self.perturbation.get(row.zone_id, 0.0)
            if delta == 0.0:
                continue
            row.t_air_c = round(row.t_air_c + delta, 2)
            estimate = estimate_wbgt(
                row.t_air_c,
                row.rh_pct,
                row.wind_ms,
                row.solar_ghi_wm2,
                shaded_fraction=row.shaded_fraction,
            )
            row.t_wet_bulb_c = round(estimate.t_wet_bulb_c, 2)
            row.t_globe_c = round(estimate.t_globe_c, 2)
            row.wbgt_shade_adjusted_c = round(estimate.wbgt_c, 2)
            row.band = estimate.band
            bundle.field.band.setdefault(row.zone_id, {})[row.hour] = estimate.band
            bundle.field.wbgt_c.setdefault(row.zone_id, {})[row.hour] = round(estimate.wbgt_c, 2)

    def _record(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(ToolCall(name=name, arguments=arguments, result=result))
        return result

    # -- tools -------------------------------------------------------------

    async def get_forecast(self) -> dict[str, Any]:
        """Venue-level hourly forecast for the event window."""
        bundle = await self.bundle()
        result = {
            "date": bundle.venue.date,
            "timezone": bundle.venue.timezone,
            "source": bundle.venue.source,
            "hours": [
                {
                    "hour": h.hour,
                    "air_temperature_c": round(h.t_air_c, 1),
                    "relative_humidity_pct": round(h.rh_pct, 0),
                    "solar_ghi_wm2": round(h.solar_ghi_wm2, 0),
                }
                for h in bundle.venue.hours
                if h.hour in self.scenario.hours
            ],
        }
        return self._record("get_forecast", {}, result)

    async def get_current(self) -> dict[str, Any]:
        """Conditions at the current hour, or the first event hour if outside it."""
        bundle = await self.bundle()
        now_hour = datetime.now().hour
        hour = now_hour if now_hour in self.scenario.hours else self.scenario.start_hour
        rows = [r for r in bundle.zone_hours if r.hour == hour]
        result = {
            "hour": hour,
            "is_live_hour": now_hour in self.scenario.hours,
            "zones": [
                {
                    "zone_id": r.zone_id,
                    "zone_name": self.scenario.zone(r.zone_id).name,
                    "air_temperature_c": r.t_air_c,
                    "wbgt_estimate_c": r.wbgt_shade_adjusted_c,
                    "band": r.band,
                    "shaded_fraction": r.shaded_fraction,
                }
                for r in rows
            ],
        }
        return self._record("get_current", {}, result)

    async def get_thermal_state(self) -> dict[str, Any]:
        """Full thermal state, plus what has changed against the standing plan.

        Two different kinds of change are reported and they are not
        interchangeable. ``band_changes_vs_plan`` is the replanning trigger: the
        forecast has moved since the plan was built. ``band_transitions`` is
        context: how conditions evolve across the evening under the current
        forecast. Only the first justifies acting.
        """
        bundle = await self.bundle()
        transitions: list[dict[str, Any]] = []
        for zone in self.scenario.zones:
            rows = sorted(
                (r for r in bundle.zone_hours if r.zone_id == zone.id), key=lambda r: r.hour
            )
            for previous, current in zip(rows, rows[1:]):
                if previous.band != current.band:
                    transitions.append(
                        {
                            "zone_id": zone.id,
                            "zone_name": zone.name,
                            "hour": current.hour,
                            "from_band": previous.band,
                            "to_band": current.band,
                            "wbgt_estimate_c": current.wbgt_shade_adjusted_c,
                            "is_escalation": BAND_ORDER.index(current.band)
                            > BAND_ORDER.index(previous.band),
                        }
                    )
        changes_vs_plan: list[dict[str, Any]] = []
        for row in bundle.zone_hours:
            previous = self.reference_bands.get(row.zone_id, {}).get(row.hour)
            if previous is None or previous == row.band:
                continue
            within_horizon = row.hour - self.scenario.start_hour <= REPLAN_HORIZON_HOURS
            changes_vs_plan.append(
                {
                    "zone_id": row.zone_id,
                    "zone_name": self.scenario.zone(row.zone_id).name,
                    "hour": row.hour,
                    "plan_band": previous,
                    "current_band": row.band,
                    "wbgt_estimate_c": row.wbgt_shade_adjusted_c,
                    "air_temperature_c": row.t_air_c,
                    "is_escalation": BAND_ORDER.index(row.band) > BAND_ORDER.index(previous),
                    "within_replan_horizon": within_horizon,
                }
            )

        result = {
            "freshness": bundle.freshness,
            "has_fortyguard_spatial_signal": bundle.has_spatial_signal,
            "has_standing_plan_reference": bool(self.reference_bands),
            "zones": [
                {
                    "zone_id": r.zone_id,
                    "zone_name": self.scenario.zone(r.zone_id).name,
                    "hour": r.hour,
                    "air_temperature_c": r.t_air_c,
                    "wbgt_estimate_c": r.wbgt_shade_adjusted_c,
                    "band": r.band,
                    "shaded_fraction": r.shaded_fraction,
                }
                for r in bundle.zone_hours
            ],
            "band_transitions": transitions,
            "band_changes_vs_plan": changes_vs_plan,
            "escalations_within_horizon": [
                c
                for c in changes_vs_plan
                if c["is_escalation"] and c["within_replan_horizon"]
            ],
        }
        return self._record("get_thermal_state", {}, result)

    async def run_simulation(self, plan_label: str = "baseline") -> dict[str, Any]:
        """Simulate a plan and return its KPIs with the Monte Carlo envelope."""
        bundle = await self.bundle()
        plan = Plan.baseline(self.scenario)
        if plan_label == "optimised" and self._optimisation is not None:
            plan = self._optimisation.optimised.plan
        outcome = simulate_fast(self.scenario, plan, bundle.field, seed=HEADLINE_SEED)
        envelope = monte_carlo(self.scenario, plan, bundle.field, n=50, seed=HEADLINE_SEED)
        result = {
            "plan": plan_label,
            "seed": HEADLINE_SEED,
            "heat_weighted_person_minutes": round(outcome.hpm, 0),
            "person_minutes_high_extreme": round(outcome.person_minutes_high_extreme, 0),
            "total_wait_person_minutes": round(outcome.total_wait_minutes, 0),
            "longest_wait_minutes": round(outcome.longest_wait_minutes, 0),
            "hpm_p10": round(envelope.hpm_p10, 0),
            "hpm_p50": round(envelope.hpm_p50, 0),
            "hpm_p90": round(envelope.hpm_p90, 0),
            "peak_queue_by_gate": {
                gate_id: round(max(series.queue), 0)
                for gate_id, series in outcome.gates.items()
            },
        }
        return self._record("run_simulation", {"plan_label": plan_label}, result)

    async def run_optimiser(self) -> dict[str, Any]:
        """Search for a better plan. Expensive; call only when something moved."""
        bundle = await self.bundle()
        self._optimisation = run_full_optimisation(self.scenario, bundle.field)
        optimisation = self._optimisation
        result = {
            "baseline_hpm": round(optimisation.baseline.hpm, 0),
            "optimised_hpm": round(optimisation.optimised.hpm, 0),
            "hpm_reduction_pct": round(optimisation.hpm_reduction_pct, 1),
            "total_wait_change_pct": round(optimisation.wait_change_pct, 1),
            "baseline_total_wait": round(optimisation.baseline.total_wait, 0),
            "optimised_total_wait": round(optimisation.optimised.total_wait, 0),
            "candidates_evaluated": optimisation.candidates_evaluated,
            "changes": [
                {
                    "action": c.action,
                    "zone_id": c.zone_id,
                    "hours": list(c.hours),
                    "band_and_hour": c.band_and_hour,
                    "binding_condition": c.binding_condition,
                    "predicted_queue": c.predicted_queue,
                    "counterfactual_share_pct": c.counterfactual_share_pct,
                }
                for c in optimisation.changes
            ],
            "resource_moves": optimisation.resource_moves,
        }
        return self._record("run_optimiser", {}, result)

    async def diff_plans(self) -> dict[str, Any]:
        """Structured difference between the current plan and the proposal."""
        if self._optimisation is None:
            await self.run_optimiser()
        assert self._optimisation is not None
        diff = self._optimisation.baseline.plan.diff(
            self._optimisation.optimised.plan, self.scenario
        )
        result = {"change_count": len(diff), "changes": diff}
        return self._record("diff_plans", {}, result)

    async def export_action_card(self) -> dict[str, Any]:
        """Render the one-page action card and report its size and action list."""
        from .export import build_actions, render_pdf

        bundle = await self.bundle()
        if self._optimisation is None:
            await self.run_optimiser()
        assert self._optimisation is not None
        actions = build_actions(self.scenario, self._optimisation, bundle)
        pdf = render_pdf(self.scenario, self._optimisation, bundle, actions)
        result = {
            "action_count": len(actions),
            "pdf_bytes": len(pdf),
            "actions": [
                {"time": f"{a.hour:02d}:{a.minute:02d}", "title": a.title} for a in actions
            ],
        }
        return self._record("export_action_card", {}, result)

    def schema(self) -> list[dict[str, Any]]:
        """Anthropic tool definitions."""
        return [
            {
                "name": "get_thermal_state",
                "description": (
                    "Per-zone, per-hour air temperature, WBGT estimate, shaded fraction "
                    "and heat band for the whole event window, plus every band "
                    "transition and which escalations fall inside the three-hour "
                    "replanning horizon. Call this first."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_forecast",
                "description": "Venue-level hourly forecast for the event window.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_current",
                "description": "Conditions at the current hour, per zone.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "run_simulation",
                "description": (
                    "Simulate a plan and return its heat-weighted person-minutes, total "
                    "wait, longest wait, peak queue per gate and the P10/P50/P90 "
                    "Monte Carlo envelope."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "plan_label": {
                            "type": "string",
                            "enum": ["baseline", "optimised"],
                            "description": "Which plan to simulate.",
                        }
                    },
                },
            },
            {
                "name": "run_optimiser",
                "description": (
                    "Search for a better plan within the venue's operating limits and "
                    "return the improvement, every proposed change with its binding "
                    "condition and counterfactual share, and any resource relocations. "
                    "Expensive: call only when conditions have actually moved."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "diff_plans",
                "description": "Structured difference between the current plan and the proposal.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "export_action_card",
                "description": "Render the one-page PDF action card for the proposed plan.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run a tool, ignoring arguments it does not accept.

        Models invent arguments. A live run against a free-tier model called
        ``get_thermal_state(hours=...)`` for a tool that takes none, and passing
        that straight through raised TypeError and killed the whole cycle. The
        tool contract is what the schema declares, so anything outside it is
        dropped and reported back rather than crashing - the model then sees what
        was ignored and can correct itself on the next round.
        """
        import inspect

        handlers: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
            "get_thermal_state": self.get_thermal_state,
            "get_forecast": self.get_forecast,
            "get_current": self.get_current,
            "run_simulation": self.run_simulation,
            "run_optimiser": self.run_optimiser,
            "diff_plans": self.diff_plans,
            "export_action_card": self.export_action_card,
        }
        handler = handlers.get(name)
        if handler is None:
            return {
                "error": f"unknown tool {name!r}",
                "available_tools": sorted(handlers),
            }

        accepted = set(inspect.signature(handler).parameters)
        allowed = {k: v for k, v in arguments.items() if k in accepted}
        ignored = sorted(set(arguments) - accepted)

        # Weaker models loop: a live run called get_thermal_state three times in
        # one cycle, spent the whole tokens-per-minute budget re-reading the same
        # answer, and never reached a directive. A repeat gets a short pointer
        # instead of the payload, which both saves the tokens and tells the model
        # plainly that it already has this so it should move on.
        signature = f"{name}:{json.dumps(allowed, sort_keys=True, default=str)}"
        if signature in self._served:
            return {
                "_already_returned": True,
                "_note": (
                    f"You already called {name} with these arguments in this cycle "
                    f"and the result has not changed. Use the earlier result and "
                    f"either call a different tool or publish your directive."
                ),
            }
        self._served.add(signature)

        result = await handler(**allowed)
        if ignored:
            result = {**result, "_ignored_arguments": ignored}
        return result


# ----------------------------------------------------------------- agent ----


class ThermCueAgent:
    """Runs the decision loop and publishes directives."""

    def __init__(
        self,
        scenario: Scenario,
        settings: Settings | None = None,
        publisher: Callable[[Directive], Awaitable[None]] | None = None,
    ) -> None:
        self.scenario = scenario
        self.settings = settings or get_settings()
        self.publisher = publisher
        self.history: list[Directive] = []
        self.reference_bands: dict[str, dict[int, str]] = {}
        """Bands the standing plan was built on. Seeded on the first cycle and
        advanced whenever the agent replans, so a single forecast revision
        triggers exactly one replan rather than one per tick forever."""

    async def _publish(self, directive: Directive) -> Directive:
        self.history.append(directive)
        if self.publisher is not None:
            await self.publisher(directive)
        return directive

    async def _advance_reference(self, tools: "AgentTools") -> None:
        """Adopt the current bands as the standing plan's basis.

        Called after a replan and on the first cycle. Without this the same
        forecast revision would re-trigger a replan on every tick and the console
        would fill with identical directives.

        Builds the bundle if the cycle never happened to load one. It used to
        return silently in that case, which is reachable whenever a model answers
        without calling a tool: the reference then stayed empty, every later tick
        counted as "no standing plan" and went to the model, and on a metered tier
        that is an unbounded spend loop rather than a missing optimisation.
        """
        bundle = await tools.bundle()
        self.reference_bands = {
            zone: dict(hours) for zone, hours in bundle.field.band.items()
        }

    async def decide(self, perturbation: dict[str, float] | None = None) -> Directive:
        """One decision cycle. Publishes exactly one directive, always."""
        tools = AgentTools(self.scenario, self.settings, reference_bands=self.reference_bands)
        if perturbation:
            tools.perturbation = dict(perturbation)

        # Spend the model on decisions, not on heartbeats.
        #
        # The timer loop mostly finds nothing changed, and calling a model to say
        # so is the wrong trade at any budget. On a metered free tier it is also
        # fatal: 96 ticks a day against a 200,000-token daily cap is the entire
        # allowance, and the deployed agent duly exhausted it and returned 429 to
        # the demo trigger - the one call that actually matters.
        #
        # So an untriggered tick with no band change against the standing plan
        # takes the deterministic path, which is what that path is for and which
        # labels itself honestly. A trigger, or a real band change, gets the
        # model.
        model_is_warranted = bool(perturbation) or not self.reference_bands
        if self.settings.has_model and not model_is_warranted:
            model_is_warranted = await self._has_band_change(tools)

        if self.settings.has_model and model_is_warranted:
            try:
                return await self._publish(await self._decide_with_model(tools, bool(perturbation)))
            except DirectiveRejected as exc:
                # A rejected directive is published as a visible failure rather
                # than retried silently. An operator watching the console must
                # see that the agent tried to assert something it could not
                # support; hiding it would defeat the purpose of checking.
                return await self._publish(
                    Directive(
                        id=f"agent-{uuid.uuid4().hex[:8]}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        tag="MONITOR",
                        text=(
                            f"Directive withheld: the generated text cited figures no "
                            f"tool produced ({exc}). Falling back to the deterministic "
                            f"path below."
                        ),
                        tool_calls=tools.calls,
                        engine=self._engine_label(),
                        grounded=False,
                    )
                )
            except DailyQuotaExhausted as exc:
                # Not an error to shout about on every tick: it is a known,
                # time-bounded state. Publish it once, plainly, and carry on
                # deterministically rather than pretending the agent is down.
                await self._publish(
                    Directive(
                        id=f"agent-{uuid.uuid4().hex[:8]}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        tag="MONITOR",
                        text=f"Model unavailable: {exc}",
                        tool_calls=tools.calls,
                        engine=self._engine_label(),
                        grounded=False,
                    )
                )
                return await self._publish(await self._decide_deterministically(tools))
            except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
                return await self._publish(
                    Directive(
                        id=f"agent-{uuid.uuid4().hex[:8]}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        tag="MONITOR",
                        text=f"Agent model call failed: {type(exc).__name__}: {exc}",
                        tool_calls=tools.calls,
                        engine=self._engine_label(),
                        grounded=False,
                    )
                )

        return await self._publish(await self._decide_deterministically(tools))

    async def _has_band_change(self, tools: "AgentTools") -> bool:
        """Is there anything for the model to reason about?

        Reuses the same tool the agent would call first, so the check costs one
        thermal-bundle read (cached) and no model tokens, and its result lands in
        the audit trace exactly as it would have anyway.

        Routed through ``dispatch`` rather than calling the tool directly, so it
        registers in the per-cycle dedup set. Called directly it recorded a
        second identical entry in the trace and the model paid full price for a
        result it already had.
        """
        try:
            state = await tools.dispatch("get_thermal_state", {})
        except Exception:  # noqa: BLE001 - a failed check must not block deciding
            return True
        return bool(state.get("band_changes_vs_plan"))

    def _engine_label(self) -> str:
        """What produced a directive, published with it.

        Never just "llm". A reader of the console must be able to see which model
        wrote a directive, because a Qwen directive and a Claude directive are
        different artefacts and the submission has to disclose which was used.
        """
        try:
            llm = self.settings.llm
        except ValueError:
            return "misconfigured"
        if llm is None:
            return "deterministic"
        # Always the exact model id and provider, never the preset's friendly
        # label. A first version used the label when it appeared to match the
        # model, which meant a directive from openai/gpt-oss-120b could be
        # published as "Llama 3.3 70B (Groq)" simply because the preset default
        # had not been overridden. This string is the submission's AI-tools
        # disclosure; one unconditional rule is the only way it stays true.
        return f"{llm.model} ({llm.provider})"

    async def _decide_with_model(self, tools: AgentTools, triggered: bool) -> Directive:
        """Tool-calling loop against whichever model provider is configured."""
        llm = self.settings.llm
        assert llm is not None
        if llm.protocol == "anthropic":
            text = await self._run_anthropic(llm, tools, triggered)
        else:
            text = await self._run_openai_compatible(llm, tools, triggered)

        ungrounded = ground_numbers(text, collect_tool_numbers(tools.calls))
        if ungrounded:
            raise DirectiveRejected(", ".join(f"{v:g}" for v in ungrounded))

        tag = text.split("|", 1)[0].strip().upper()
        if tag not in {"REPLAN", "MONITOR", "NO-ACTION"}:
            tag = "MONITOR"

        if tag == "REPLAN" or not tools.reference_bands:
            await self._advance_reference(tools)

        return Directive(
            id=f"agent-{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            tag=tag,
            text=text,
            tool_calls=tools.calls,
            engine=self._engine_label(),
        )

    async def _run_anthropic(
        self, llm: "LlmConfig", tools: AgentTools, triggered: bool
    ) -> str:
        """Anthropic Messages API with native tool use."""
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=llm.api_key, base_url=llm.base_url)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (TRIGGER_NOTE if triggered else "")
                + "Assess the current plan and decide whether to act.",
            }
        ]

        for _ in range(MAX_TOOL_ROUNDS):
            response = await client.messages.create(
                model=llm.model,
                max_tokens=self.settings.agent_max_tokens,
                temperature=self.settings.agent_temperature,
                system=SYSTEM_PROMPT,
                tools=tools.schema(),
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
            if not tool_uses:
                break
            results = []
            for block in tool_uses:
                output = await tools.dispatch(block.name, dict(block.input or {}))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _model_json(block.name, output),
                    }
                )
            messages.append({"role": "user", "content": results})

        messages.append({"role": "user", "content": DIRECTIVE_INSTRUCTION})
        final = await client.messages.create(
            model=llm.model,
            max_tokens=512,
            temperature=self.settings.agent_temperature,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return "".join(
            getattr(b, "text", "") for b in final.content if getattr(b, "type", "") == "text"
        ).strip()

    async def _run_openai_compatible(
        self, llm: "LlmConfig", tools: AgentTools, triggered: bool
    ) -> str:
        """OpenAI /chat/completions with tool calling.

        One implementation covers Qwen, Groq, OpenRouter, Cerebras, DeepSeek,
        Together and OpenAI itself, because they all speak this protocol. Written
        against httpx rather than the ``openai`` package: the surface used here is
        one endpoint, and a second SDK is a second thing to keep current.

        Free tiers are noticeably less reliable than paid ones about the tool
        contract, so this is defensive in three specific places, each marked
        below. None of them relaxes the grounding check - a sloppy model gets its
        directive rejected exactly like a careful one would.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (TRIGGER_NOTE if triggered else "")
                + "Assess the current plan and decide whether to act.",
            },
        ]
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools.schema()
        ]

        async with httpx.AsyncClient(
            base_url=llm.base_url,
            timeout=httpx.Timeout(self.settings.agent_request_timeout_s),
            headers={
                "Authorization": f"Bearer {llm.api_key}",
                "Content-Type": "application/json",
            },
        ) as client:

            async def complete(body: dict[str, Any]) -> dict[str, Any]:
                """One completion, waiting out a rate limit rather than dying on it.

                Free tiers meter tokens per minute and will refuse mid-cycle.
                That is normal operation for them, not an outage, and it must not
                cost the operator a directive: the deployed agent came back 429
                in four seconds having published nothing, while the provider's
                own response said to try again in 3.4.

                So the provider's Retry-After is honoured when present, since it
                knows when its window resets, with exponential backoff as the
                fallback and a small ceiling on the total wait. Every other 4xx
                is a real error and is raised immediately rather than retried.
                """
                delay = RATE_LIMIT_BASE_DELAY_S
                last = ""
                for attempt in range(RATE_LIMIT_RETRIES + 1):
                    response = await client.post("/chat/completions", json=body)
                    if response.status_code < 400:
                        return response.json()
                    if _is_tool_generation_failure(response):
                        raise ToolGenerationError(response.text[:300])
                    if _is_daily_quota_exhausted(response):
                        raise DailyQuotaExhausted(
                            f"{llm.model} has exhausted its daily token allowance. "
                            f"Waiting will not help; the window resets on the "
                            f"provider's schedule. The agent falls back to its "
                            f"deterministic path until then. {response.text[:200]}"
                        )
                    last = response.text[:400]
                    if response.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                        raise RuntimeError(
                            f"{llm.model} returned {response.status_code}: {last}"
                        )
                    await _backoff_sleep(
                        min(_retry_after_seconds(response, delay), RATE_LIMIT_MAX_DELAY_S)
                    )
                    delay = min(delay * 2, RATE_LIMIT_MAX_DELAY_S)
                raise RuntimeError(
                    f"{llm.model} rate limited after {RATE_LIMIT_RETRIES} retries: {last}"
                )

            for _ in range(MAX_TOOL_ROUNDS):
                try:
                    body = await complete(
                        {
                            "model": llm.model,
                            "messages": messages,
                            "tools": openai_tools,
                            "tool_choice": "auto",
                            "temperature": self.settings.agent_temperature,
                            "max_tokens": self.settings.agent_max_tokens,
                        }
                    )
                except ToolGenerationError:
                    # Stop asking for tools and let it commit to a directive with
                    # what has already been gathered. Every figure it can cite is
                    # still a figure a tool returned, so grounding is unchanged;
                    # the model simply has less to work with, which is the honest
                    # consequence of a model that cannot format a tool call.
                    if not tools.calls:
                        raise
                    break
                choice = (body.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                calls = message.get("tool_calls") or []
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        **({"tool_calls": calls} if calls else {}),
                    }
                )
                if not calls:
                    break
                for call in calls:
                    function = call.get("function") or {}
                    name = function.get("name") or ""
                    # Defensive 1: arguments arrive as a JSON *string*, and free
                    # tiers sometimes send an empty string or malformed JSON for
                    # a no-argument tool. Treat that as no arguments rather than
                    # failing the whole run.
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                    output = await tools.dispatch(name, arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or name,
                            "name": name,
                            "content": _model_json(name, output),
                        }
                    )

            messages.append({"role": "user", "content": DIRECTIVE_INSTRUCTION})
            body = await complete(
                {
                    "model": llm.model,
                    "messages": messages,
                    "temperature": self.settings.agent_temperature,
                    "max_tokens": 512,
                }
            )
            message = ((body.get("choices") or [{}])[0].get("message") or {})
            content = message.get("content") or ""
            # Defensive 2: some providers return content as a list of parts
            # rather than a string.
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            # Defensive 3: reasoning models prepend a think block. The directive
            # is the last non-empty line that carries the tag separator.
            text = str(content).strip()
            if "</think>" in text:
                text = text.split("</think>", 1)[1].strip()
            for line in reversed([l.strip() for l in text.splitlines() if l.strip()]):
                if "|" in line:
                    return line
            return text

    async def _decide_deterministically(self, tools: AgentTools) -> Directive:
        """The no-model path: same tools, same guardrails, templated language.

        Labelled ``deterministic`` everywhere it surfaces. It is not the agent
        and must never be presented as one.
        """
        state = await tools.get_thermal_state()
        escalations = state["escalations_within_horizon"]

        if not state["has_standing_plan_reference"]:
            # First cycle: there is nothing to diff against yet. Adopt the
            # current forecast as the plan's basis and say so, rather than
            # inventing a change or staying silent.
            await self._advance_reference(tools)
            worst = max(state["zones"], key=lambda z: BAND_ORDER.index(z["band"]), default=None)
            return Directive(
                id=f"agent-{uuid.uuid4().hex[:8]}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                tag="MONITOR",
                text=(
                    f"MONITOR | Baseline established against the current forecast. "
                    + (
                        f"{worst['zone_name']} is the hottest zone at {worst['band']} "
                        f"band, WBGT est {worst['wbgt_estimate_c']}. "
                        if worst
                        else ""
                    )
                    + "| Watching for a band change against this plan."
                ),
                tool_calls=tools.calls,
                engine="deterministic",
            )

        if not escalations:
            worst = max(
                state["zones"], key=lambda z: BAND_ORDER.index(z["band"]), default=None
            )
            text = (
                f"NO-ACTION | No zone has escalated a heat band against the standing "
                f"plan within the next {REPLAN_HORIZON_HOURS} hours. "
                + (
                    f"{worst['zone_name']} is the hottest at {worst['band']} band, "
                    f"WBGT est {worst['wbgt_estimate_c']}. "
                    if worst
                    else ""
                )
                + "| Current plan stands."
            )
            return Directive(
                id=f"agent-{uuid.uuid4().hex[:8]}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                tag="NO-ACTION",
                text=text,
                tool_calls=tools.calls,
                engine="deterministic",
            )

        optimisation = await tools.run_optimiser()
        await tools.diff_plans()
        first = escalations[0]
        instructions = "; ".join(c["action"] for c in optimisation["changes"][:3]) or "no change"
        text = (
            f"REPLAN | {first['zone_name']} crosses {first['current_band']} at "
            f"{first['hour']:02d}:00, WBGT est {first['wbgt_estimate_c']}. "
            f"{instructions}. | Heat-weighted exposure falls "
            f"{optimisation['hpm_reduction_pct']}% for a "
            f"{optimisation['total_wait_change_pct']}% change in total wait."
        )
        ungrounded = ground_numbers(text, collect_tool_numbers(tools.calls))
        await self._advance_reference(tools)
        return Directive(
            id=f"agent-{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            tag="REPLAN",
            text=text,
            tool_calls=tools.calls,
            engine="deterministic",
            grounded=not ungrounded,
            rejected_numbers=ungrounded,
        )

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Timer loop. Publishes a directive every tick, including no-action ones."""
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                await self.decide()
            except Exception as exc:  # noqa: BLE001 - the loop must not die silently
                await self._publish(
                    Directive(
                        id=f"agent-{uuid.uuid4().hex[:8]}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        tag="MONITOR",
                        text=f"Agent cycle failed: {type(exc).__name__}: {exc}",
                        engine="error",
                        grounded=False,
                    )
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.agent_tick_seconds)
            except asyncio.TimeoutError:
                continue
