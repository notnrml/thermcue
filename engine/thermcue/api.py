"""HTTP and WebSocket surface.

Endpoints match the brief: ``/scenario``, ``/thermal``, ``/simulate``,
``/optimise``, ``/pareto``, ``WS /agent``, ``/validation``, ``/export/pdf|ics``,
plus ``/health``, ``/plan`` (the whole Plan Workspace payload in one call) and
``/credits``.

Two things this module is strict about.

**Nothing is computed twice.** The thermal pipeline and the optimiser are both
expensive and both deterministic given the scenario and the cache, so results are
memoised behind an async lock. Without it, four widgets loading in parallel would
launch four optimiser searches and the first paint would take a minute.

**Freshness is never inferred.** Every payload carries the freshness its data
actually had, so the UI's Live/Cached badge reflects the pipeline rather than
whether the server happened to be online.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from . import __version__
from .agent import Directive, ThermCueAgent
from .config import get_settings
from .export import build_actions, render_ics, render_pdf
from .fortyguard import CreditLedger
from .models import (
    AgentFeedEntry,
    Gate,
    HourlyZoneState,
    KpiComparison,
    KpiSet,
    PlanChange,
    PlanWorkspaceData,
    Resource,
    ScenarioEvent,
    TimeWindow,
    ValidationResponse,
    WbgtHourly,
    WhyTraceStep,
    Zone,
)
from .optimise import OptimisationResult, run_full_optimisation
from .plan import Plan
from .scenario import Scenario, ScenarioError, load_scenario
from .service import ThermalBundle, build_thermal_bundle
from .simulate import HEADLINE_SEED, monte_carlo, simulate_fast, weight_sensitivity
from .validation import ValidationOutcome, build_validation


@dataclass
class EngineState:
    """Memoised pipeline results plus the locks that keep them singular."""

    scenario: Scenario
    bundle: ThermalBundle | None = None
    optimisation: OptimisationResult | None = None
    validation: ValidationOutcome | None = None
    agent: ThermCueAgent | None = None
    bundle_lock: asyncio.Lock | None = None
    optimise_lock: asyncio.Lock | None = None
    validation_lock: asyncio.Lock | None = None

    async def get_bundle(self, refresh: bool = False) -> ThermalBundle:
        assert self.bundle_lock is not None
        async with self.bundle_lock:
            if self.bundle is None or refresh:
                self.bundle = await build_thermal_bundle(self.scenario, refresh=refresh)
                # A new thermal field invalidates everything downstream. Serving a
                # stale optimisation against fresh temperatures would show a plan
                # justified by conditions no longer on screen.
                self.optimisation = None
                self.validation = None
            return self.bundle

    async def get_optimisation(self, refresh: bool = False) -> OptimisationResult:
        assert self.optimise_lock is not None
        bundle = await self.get_bundle()
        async with self.optimise_lock:
            if self.optimisation is None or refresh:
                # The optimiser is CPU-bound for several seconds. Off the event
                # loop, or the WebSocket console freezes while it runs.
                self.optimisation = await asyncio.to_thread(
                    run_full_optimisation, self.scenario, bundle.field
                )
            return self.optimisation

    async def get_validation(self) -> ValidationOutcome:
        assert self.validation_lock is not None
        bundle = await self.get_bundle()
        async with self.validation_lock:
            if self.validation is None:
                self.validation = await build_validation(self.scenario, bundle)
            return self.validation


class AgentHub:
    """Fan-out for agent directives over WebSocket."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.backlog: list[Directive] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.add(websocket)
        # Replay the backlog so a console opened mid-event is not blank. An empty
        # feed reads as "the agent has done nothing", which is a different and
        # false claim from "you just arrived".
        for directive in self.backlog[-20:]:
            await websocket.send_json(_directive_payload(directive))

    def disconnect(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)

    async def publish(self, directive: Directive) -> None:
        self.backlog.append(directive)
        payload = _directive_payload(directive)
        dead: list[WebSocket] = []
        for client in list(self.clients):
            try:
                await client.send_json(payload)
            except (WebSocketDisconnect, RuntimeError):
                dead.append(client)
        for client in dead:
            self.disconnect(client)


def _directive_payload(directive: Directive) -> dict[str, Any]:
    entry = directive.to_feed_entry()
    return {
        **entry.model_dump(by_alias=True),
        "tag": directive.tag,
        "engine": directive.engine,
        "promptVersion": directive.prompt_version,
        "grounded": directive.grounded,
        "rejectedNumbers": directive.rejected_numbers,
    }


hub = AgentHub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        scenario = load_scenario()
    except ScenarioError as exc:
        # Failing at startup with a precise message beats failing halfway through
        # an optimiser run in front of an audience.
        raise RuntimeError(f"Scenario failed validation at startup: {exc}") from exc

    state = EngineState(
        scenario=scenario,
        bundle_lock=asyncio.Lock(),
        optimise_lock=asyncio.Lock(),
        validation_lock=asyncio.Lock(),
    )
    state.agent = ThermCueAgent(scenario, settings, publisher=hub.publish)
    app.state.engine = state
    app.state.ledger = CreditLedger(settings.cache_dir / "credit_ledger.jsonl")

    loop_stop = asyncio.Event()
    loop_task = asyncio.create_task(state.agent.run_forever(loop_stop))
    try:
        yield
    finally:
        loop_stop.set()
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


app = FastAPI(
    title="ThermCue engine",
    version=__version__,
    description=(
        "Heat-aware crowd-flow planning for outdoor mass-gathering events, built on "
        "the FortyGuard tOS Enterprise API."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def engine(request_app: FastAPI) -> EngineState:
    return request_app.state.engine


# ------------------------------------------------------------ conversion ----


def _scenario_payload(scenario: Scenario, bundle: ThermalBundle) -> ScenarioEvent:
    """Scenario in the UI's shape, with live thermal values folded in.

    Zone temperature and band are taken at the hottest event hour rather than the
    first: the map's default view should open on the moment that matters.
    """
    peak_hour = max(
        scenario.hours,
        key=lambda h: max(
            (r.wbgt_shade_adjusted_c for r in bundle.zone_hours if r.hour == h), default=0.0
        ),
    )
    lookup = {(r.zone_id, r.hour): r for r in bundle.zone_hours}
    return ScenarioEvent(
        id=scenario.id,
        venue=scenario.venue,
        date=scenario.date,
        time_window=TimeWindow(start_hour=scenario.start_hour, end_hour=scenario.end_hour),
        timezone=scenario.timezone,
        data_freshness=bundle.freshness,
        zones=[
            Zone(
                id=z.id,
                name=z.name,
                polygon=[tuple(p) for p in z.polygon],
                wbgt_band=(lookup.get((z.id, peak_hour)).band if lookup.get((z.id, peak_hour)) else "low"),
                temperature_c=(
                    lookup.get((z.id, peak_hour)).t_air_c if lookup.get((z.id, peak_hour)) else 0.0
                ),
                shade_coverage=(
                    lookup.get((z.id, peak_hour)).shaded_fraction
                    if lookup.get((z.id, peak_hour))
                    else z.built_shade_fraction
                ),
            )
            for z in scenario.zones
        ],
        gates=[],
        resources=[
            Resource(
                id=r.id, type=r.type, name=r.name, coordinates=tuple(r.coordinates), movable=r.movable
            )
            for r in scenario.resources
        ],
    )


def _gate_payload(scenario: Scenario, result, peak_hour: int) -> list[Gate]:
    rows = {(r["gate_id"], r["hour"]): r for r in result.hourly_rows()}
    gates: list[Gate] = []
    for gate in scenario.gates:
        row = rows.get((gate.id, peak_hour), {})
        series = result.gates[gate.id]
        start = (peak_hour - scenario.start_hour) * 60
        window = series.queue[start : start + 60] or [0.0]
        gates.append(
            Gate(
                id=gate.id,
                name=gate.name,
                coordinates=tuple(gate.coordinates),
                capacity=int(scenario.service.capacity_per_hour(gate.staff_count)) or 1,
                lanes=max(scenario.service.lanes_for(gate.staff_count), 1),
                staff_count=gate.staff_count,
                queue_length=int(round(max(window))),
                wait_time_minutes=float(row.get("wait_time_minutes", 0.0)),
            )
        )
    return gates


def _plan_changes(result: OptimisationResult) -> list[PlanChange]:
    changes: list[PlanChange] = []
    for change in result.changes:
        changes.append(
            PlanChange(
                id=change.id,
                kind=change.kind if change.kind in ("gate", "staff", "water", "rest") else "gate",
                action=change.action,
                time_chips=[f"{h:02d}:00" for h in change.hours],
                why_trace=[
                    WhyTraceStep(stage="Forecast", detail=change.band_and_hour),
                    WhyTraceStep(stage="Binding condition", detail=change.binding_condition),
                    WhyTraceStep(
                        stage="Predicted queue",
                        detail=f"{change.predicted_queue:.0f} people under the unchanged plan",
                    ),
                    WhyTraceStep(stage="Action", detail=change.action),
                    WhyTraceStep(
                        stage="Effect",
                        detail=(
                            f"Heat-weighted person-minutes fall by "
                            f"{change.hpm_delta:,.0f} when this change is kept, measured "
                            f"by removing it and re-simulating."
                        ),
                    ),
                ],
                counterfactual_percent=change.counterfactual_share_pct,
            )
        )
    for move in result.resource_moves:
        changes.append(
            PlanChange(
                id=f"chg-{move['resource_id']}",
                kind=move["type"],
                action=f"Relocate {move['resource_name']} to {move['to_zone']}",
                time_chips=[f"{move['hour']:02d}:00"],
                why_trace=[
                    WhyTraceStep(stage="Binding condition", detail=move["binding_condition"]),
                    WhyTraceStep(
                        stage="Effect",
                        detail=(
                            "Scored against relief coverage, not heat-weighted "
                            "person-minutes: a water point does not shorten a queue."
                        ),
                    ),
                ],
                counterfactual_percent=0.0,
            )
        )
    return changes


def _kpis(result: OptimisationResult) -> KpiComparison:
    return KpiComparison(
        baseline=KpiSet(
            heat_weighted_person_minutes=round(result.baseline.hpm, 1),
            person_minutes_high_extreme=round(result.baseline.result.person_minutes_high_extreme, 1),
            total_wait_minutes=round(result.baseline.total_wait, 1),
            longest_wait_minutes=round(result.baseline.result.longest_wait_minutes, 1),
        ),
        optimised=KpiSet(
            heat_weighted_person_minutes=round(result.optimised.hpm, 1),
            person_minutes_high_extreme=round(result.optimised.result.person_minutes_high_extreme, 1),
            total_wait_minutes=round(result.optimised.total_wait, 1),
            longest_wait_minutes=round(result.optimised.result.longest_wait_minutes, 1),
        ),
    )


# -------------------------------------------------------------- endpoints ---


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness plus an honest statement of which sources are configured.

    A demo that is up but has no FortyGuard key is a different state from a demo
    that is working, and the difference must be visible without reading logs.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "fortyguard_key_configured": settings.has_fortyguard_key,
        "anthropic_key_configured": settings.has_anthropic_key,
        "offline_mode": settings.offline,
    }


@app.get("/scenario")
async def get_scenario_endpoint() -> dict[str, Any]:
    state = engine(app)
    bundle = await state.get_bundle()
    scenario = state.scenario
    result = simulate_fast(scenario, Plan.baseline(scenario), bundle.field, seed=HEADLINE_SEED)
    peak_hour = max(
        scenario.hours,
        key=lambda h: max((r.wbgt_shade_adjusted_c for r in bundle.zone_hours if r.hour == h), default=0.0),
    )
    payload = _scenario_payload(scenario, bundle)
    payload.gates = _gate_payload(scenario, result, peak_hour)
    return {
        "scenario": payload.model_dump(by_alias=True),
        "notes": list(scenario.notes) + bundle.notes,
        "sources": bundle.sources,
    }


@app.get("/thermal")
async def get_thermal(refresh: bool = Query(default=False)) -> dict[str, Any]:
    state = engine(app)
    bundle = await state.get_bundle(refresh=refresh)
    return {
        "zones": [z.model_dump(by_alias=True) for z in bundle.zone_hours],
        "hourlyZoneStates": [
            HourlyZoneState(
                zone_id=z.zone_id,
                hour=z.hour,
                wbgt_band=z.band,
                temperature_c=z.t_air_c,
                shade_coverage=z.shaded_fraction,
            ).model_dump(by_alias=True)
            for z in bundle.zone_hours
        ],
        "freshness": bundle.freshness,
        "hasFortyguardSpatialSignal": bundle.has_spatial_signal,
        "analogueDay": (
            {
                "date": bundle.analogue.date,
                "rmsErrorC": round(bundle.analogue.rms_error_c, 3),
                "meanBiasC": round(bundle.analogue.mean_bias_c, 3),
                "quality": bundle.analogue.quality,
                "note": bundle.analogue.note,
            }
            if bundle.analogue
            else None
        ),
        "zoneOffsetsC": bundle.offsets_c,
        "shadeMethod": bundle.shade.method,
        "buildingCount": bundle.shade.building_count,
        "assumedHeightCount": bundle.shade.assumed_height_count,
        "sources": bundle.sources,
        "notes": bundle.notes,
    }


@app.post("/simulate")
async def post_simulate(
    plan: str = Query(default="baseline", pattern="^(baseline|optimised)$"),
    monte_carlo_n: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    state = engine(app)
    bundle = await state.get_bundle()
    scenario = state.scenario

    chosen = Plan.baseline(scenario)
    if plan == "optimised":
        chosen = (await state.get_optimisation()).optimised.plan

    result = simulate_fast(scenario, chosen, bundle.field, seed=HEADLINE_SEED)
    envelope = await asyncio.to_thread(
        monte_carlo, scenario, chosen, bundle.field, monte_carlo_n, HEADLINE_SEED
    )
    return {
        "plan": plan,
        "seed": HEADLINE_SEED,
        "engine": result.engine,
        "queueStates": [
            {
                "gateId": r["gate_id"],
                "hour": r["hour"],
                "arrivals": r["arrivals"],
                "waitTimeMinutes": r["wait_time_minutes"],
                "personMinutes": r["person_minutes"],
            }
            for r in result.hourly_rows()
        ],
        "kpis": {
            "heatWeightedPersonMinutes": round(result.hpm, 1),
            "personMinutesHighExtreme": round(result.person_minutes_high_extreme, 1),
            "totalWaitMinutes": round(result.total_wait_minutes, 1),
            "longestWaitMinutes": round(result.longest_wait_minutes, 1),
            "unservedAtClose": round(result.unserved_at_close, 1),
        },
        "monteCarlo": {
            "n": envelope.n,
            "seed": envelope.seed,
            "hpmP10": round(envelope.hpm_p10, 1),
            "hpmP50": round(envelope.hpm_p50, 1),
            "hpmP90": round(envelope.hpm_p90, 1),
            "waitP10": round(envelope.wait_p10, 1),
            "waitP50": round(envelope.wait_p50, 1),
            "waitP90": round(envelope.wait_p90, 1),
        },
    }


@app.post("/optimise")
async def post_optimise(refresh: bool = Query(default=False)) -> dict[str, Any]:
    state = engine(app)
    result = await state.get_optimisation(refresh=refresh)
    bundle = await state.get_bundle()
    sensitivity = await asyncio.to_thread(
        weight_sensitivity,
        state.scenario,
        result.baseline.plan,
        result.optimised.plan,
        bundle.field,
        HEADLINE_SEED,
    )
    return {
        "kpis": _kpis(result).model_dump(by_alias=True),
        "hpmReductionPct": round(result.hpm_reduction_pct, 2),
        "waitChangePct": round(result.wait_change_pct, 2),
        "planChanges": [c.model_dump(by_alias=True) for c in _plan_changes(result)],
        "resourceMoves": result.resource_moves,
        "candidatesEvaluated": result.candidates_evaluated,
        "weightSensitivity": sensitivity,
        "notes": result.notes,
    }


@app.get("/pareto")
async def get_pareto() -> dict[str, Any]:
    result = await engine(app).get_optimisation()
    # camelCase throughout: the TypeScript contract in web/types is the
    # authority on field names and a snake_case leak here means the chart
    # silently renders undefined rather than failing.
    return {
        "frontier": [
            {
                "waitRatio": p["wait_ratio"],
                "totalWaitMinutes": p["total_wait_minutes"],
                "heatWeightedExposure": p["heat_weighted_exposure"],
                "hpmReductionPct": p["hpm_reduction_pct"],
                "isChosen": p["is_chosen"],
            }
            for p in result.pareto
        ],
        "points": [
            {
                "id": p["id"],
                "totalWaitMinutes": p["total_wait_minutes"],
                "heatWeightedExposure": p["heat_weighted_exposure"],
                "kind": p["kind"],
            }
            for p in result.pareto_scatter
        ],
    }


@app.get("/validation")
async def get_validation_endpoint() -> dict[str, Any]:
    state = engine(app)
    outcome = await state.get_validation()
    return ValidationResponse(
        points=outcome.points,
        summary=outcome.summary,
        station_name=outcome.station_name,
        station_source=outcome.station_source,
    ).model_dump(by_alias=True) | {
        "disagreements": outcome.disagreements,
        "stationBandByHour": outcome.station_band_by_hour,
    }


@app.get("/plan")
async def get_plan_workspace() -> dict[str, Any]:
    """The whole Plan Workspace payload in one call.

    The UI's page-level contract is a single object, so serving it as one request
    avoids a first paint assembled from six round trips that can disagree with
    each other about freshness.
    """
    state = engine(app)
    scenario = state.scenario
    bundle = await state.get_bundle()
    result = await state.get_optimisation()
    validation = await state.get_validation()

    optimised = simulate_fast(
        scenario, result.optimised.plan, bundle.field, seed=HEADLINE_SEED
    )
    peak_hour = max(
        scenario.hours,
        key=lambda h: max((r.wbgt_shade_adjusted_c for r in bundle.zone_hours if r.hour == h), default=0.0),
    )
    scenario_payload = _scenario_payload(scenario, bundle)
    scenario_payload.gates = _gate_payload(scenario, optimised, peak_hour)

    envelope_by_hour: list[WbgtHourly] = []
    for hour in scenario.hours:
        values = [r.wbgt_shade_adjusted_c for r in bundle.zone_hours if r.hour == hour]
        if not values:
            continue
        ordered = sorted(values)
        envelope_by_hour.append(
            WbgtHourly(
                hour=hour,
                p10=round(ordered[0], 2),
                p50=round(ordered[len(ordered) // 2], 2),
                p90=round(ordered[-1], 2),
                venue_max=round(max(values), 2),
            )
        )

    feed: list[AgentFeedEntry] = [
        d.to_feed_entry() for d in (state.agent.history[-20:] if state.agent else [])
    ]

    payload = PlanWorkspaceData(
        scenario=scenario_payload,
        hourly_zone_states=[
            HourlyZoneState(
                zone_id=z.zone_id,
                hour=z.hour,
                wbgt_band=z.band,
                temperature_c=z.t_air_c,
                shade_coverage=z.shaded_fraction,
            )
            for z in bundle.zone_hours
        ],
        queue_states=[
            {
                "gateId": r["gate_id"],
                "hour": r["hour"],
                "arrivals": r["arrivals"],
                "waitTimeMinutes": r["wait_time_minutes"],
                "personMinutes": r["person_minutes"],
            }
            for r in optimised.hourly_rows()
        ],
        kpis=_kpis(result),
        pareto_points=result.pareto_scatter,
        plan_changes=_plan_changes(result),
        agent_feed=feed,
        validation_points=validation.points,
        validation_summary=validation.summary,
        wbgt_hourly=envelope_by_hour,
    )
    return payload.model_dump(by_alias=True) | {
        "meta": {
            "freshness": bundle.freshness,
            "hasFortyguardSpatialSignal": bundle.has_spatial_signal,
            "sources": bundle.sources,
            "notes": bundle.notes + result.notes,
            "seed": HEADLINE_SEED,
        }
    }


@app.get("/export/pdf")
async def export_pdf() -> Response:
    state = engine(app)
    bundle = await state.get_bundle()
    result = await state.get_optimisation()
    pdf = await asyncio.to_thread(render_pdf, state.scenario, result, bundle)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="thermcue-{state.scenario.id}.pdf"'
        },
    )


@app.get("/export/ics")
async def export_ics() -> Response:
    state = engine(app)
    bundle = await state.get_bundle()
    result = await state.get_optimisation()
    actions = build_actions(state.scenario, result, bundle)
    return Response(
        content=render_ics(state.scenario, actions),
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="thermcue-{state.scenario.id}.ics"'
        },
    )


@app.get("/credits")
async def get_credits() -> dict[str, Any]:
    """FortyGuard spend, logged per endpoint from the first call, per the brief."""
    return app.state.ledger.summary()


@app.post("/agent/trigger")
async def trigger_agent(
    zone_id: str = Query(...),
    delta_c: float = Query(default=2.0, ge=-10.0, le=10.0),
) -> dict[str, Any]:
    """The demo trigger: perturb one zone's forecast and let the agent react.

    Synchronous by design. The brief's acceptance gate is a correct, fully traced
    autonomous replan inside 30 seconds on the public deployment, and that is only
    demonstrable if the response carries the directive rather than a job id.
    """
    state = engine(app)
    if state.agent is None:
        raise HTTPException(status_code=503, detail="Agent is not running")
    if zone_id not in {z.id for z in state.scenario.zones}:
        raise HTTPException(status_code=404, detail=f"Unknown zone {zone_id!r}")
    directive = await state.agent.decide(perturbation={zone_id: delta_c})
    return _directive_payload(directive)


@app.get("/agent/history")
async def agent_history() -> dict[str, Any]:
    state = engine(app)
    history = state.agent.history if state.agent else []
    return {"directives": [_directive_payload(d) for d in history[-50:]]}


@app.websocket("/agent")
async def agent_socket(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        while True:
            # The console is publish-only. Reading keeps the connection alive and
            # lets the client send a ping without the server treating silence as
            # a disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except RuntimeError:
        hub.disconnect(websocket)


@app.exception_handler(ScenarioError)
async def scenario_error_handler(_request: Any, exc: ScenarioError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": f"Scenario invalid: {exc}"})
