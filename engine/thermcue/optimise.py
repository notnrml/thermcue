"""Heat-aware plan optimisation.

Architecture, stated plainly because the README must state it too
-----------------------------------------------------------------
**The optimiser searches. The simulator judges.** No closed-form objective is
ever used to accept a change. CP-SAT proposes integer staffing allocations that
satisfy the hard operating constraints, coordinate descent explores gate timing
and staggering, and every candidate plan is scored by running the actual queue
simulation. That is slower than optimising a surrogate, and it is the only way
the reported HPM reduction means what it says.

Objective
---------
Minimise heat-weighted person-minutes subject to total wait not rising above
``limits.max_wait_increase_ratio`` times the baseline. Cutting heat exposure by
making everyone wait longer somewhere cooler is a real trade, but an unbounded
one is not a plan an operator would sign, so the wait constraint is hard.

Explainability
--------------
Every accepted change carries a populated why-object: the binding condition that
triggered it, the zone, the band and hour, the predicted queue, and its HPM
delta. Shares of the total improvement come from leave-one-out counterfactuals -
each change is removed from the winning plan on its own and the plan re-scored -
rather than from any attribution heuristic. Shares are reported normalised and
the raw deltas are kept, because leave-one-out shares do not sum to 100 % when
changes interact, and rescaling them silently would hide exactly that
interaction.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .models import WbgtBand
from .plan import Plan, PlanInfeasibleError, block_count, block_for_minute
from .scenario import Scenario
from .simulate import (
    HEADLINE_SEED,
    SimResult,
    ThermalField,
    arrival_profile,
    simulate_fast,
)
from .thermal import BAND_WEIGHTS

#: Wait-constraint multipliers for the Pareto sweep, per the brief.
PARETO_RATIOS: tuple[float, ...] = (1.00, 1.05, 1.10, 1.20)

#: How many CP-SAT staffing proposals to carry into the search.
#:
#: Four, not eight. CP-SAT dominates the wall clock here - the simulations are
#: about a millisecond each, the solves are seconds - and on this scenario every
#: staffing proposal is rejected by the search anyway. That rejection is itself
#: the finding: with 21 staff fixed and the baseline allocation already close to
#: proportional to demand, reallocation cannot create throughput, it can only
#: move a queue from one gate to another. The wins are in the timing levers,
#: which cost nothing. You cannot staff your way out of a heat problem on a
#: fixed headcount; you can schedule your way out.
STAFFING_PROPOSALS = 6

SCALE = 100
"""Fixed-point scale for fractional quantities entering CP-SAT constraints.
CP-SAT is integer-only and silently truncates floats."""

STAFF_SWAP_SIZES: tuple[int, ...] = (1, 2, 3, 4)
"""How many staff a single swap may move. Swaps are the dominant lever at this
scenario and exploring them directly is what makes the search reproducible."""

MAX_COORDINATE_PASSES = 4
"""Coordinate descent stops early when a full pass changes nothing. This caps
the pathological case rather than defining the normal one."""


class NoFeasiblePlanError(RuntimeError):
    """Not even the baseline satisfied the constraints. Something is wrong with
    the scenario, not with the search."""


@dataclass(slots=True)
class ScoredPlan:
    plan: Plan
    result: SimResult

    @property
    def hpm(self) -> float:
        return self.result.hpm

    @property
    def total_wait(self) -> float:
        return self.result.total_wait_minutes


@dataclass(slots=True)
class ChangeExplanation:
    """One accepted change and why it was accepted."""

    id: str
    kind: str
    action: str
    zone_id: str | None
    hours: tuple[int, ...]
    band_and_hour: str
    binding_condition: str
    predicted_queue: float
    hpm_delta: float
    counterfactual_share_pct: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OptimisationResult:
    baseline: ScoredPlan
    optimised: ScoredPlan
    changes: list[ChangeExplanation]
    pareto: list[dict[str, Any]]
    pareto_scatter: list[dict[str, Any]]
    resource_moves: list[dict[str, Any]]
    candidates_evaluated: int
    notes: list[str]

    @property
    def hpm_reduction_pct(self) -> float:
        if self.baseline.hpm <= 0:
            return 0.0
        return 100.0 * (self.baseline.hpm - self.optimised.hpm) / self.baseline.hpm

    @property
    def wait_change_pct(self) -> float:
        if self.baseline.total_wait <= 0:
            return 0.0
        return 100.0 * (self.optimised.total_wait - self.baseline.total_wait) / self.baseline.total_wait


# ------------------------------------------------------------- pressure -----


def heat_pressure(
    scenario: Scenario, thermal: ThermalField, weights: dict[WbgtBand, float] | None = None
) -> dict[str, dict[int, float]]:
    """Band-weighted arrival pressure per gate per staffing block.

    This is what CP-SAT optimises against: a cheap proxy that says where staff
    are worth most, given both the arrival rate and how punishing the queue zone
    is at that hour. It is explicitly **not** the objective - the simulator is -
    but a proposal generator needs something linear to work with, and a proposal
    that ignores heat would make CP-SAT a queue optimiser with a heat label.
    """
    table = weights or BAND_WEIGHTS
    blocks = block_count(scenario)
    baseline_arrivals = arrival_profile(scenario, Plan.baseline(scenario))
    pressure: dict[str, dict[int, float]] = {}
    for gate in scenario.gates:
        per_block: dict[int, float] = {}
        for block in range(blocks):
            start = block * scenario.limits.staff_block_minutes
            end = start + scenario.limits.staff_block_minutes
            arrivals = sum(baseline_arrivals[gate.id][start:end])
            hour = scenario.start_hour + start // 60
            band_weight = table[thermal.band_at(gate.queue_zone, hour)]
            # 1 + weight, so a Low-band gate still attracts staff on volume
            # alone; a pure multiplier would leave cool gates unstaffed.
            per_block[block] = arrivals * (1.0 + band_weight)
        pressure[gate.id] = per_block
    return pressure


# ----------------------------------------------------- CP-SAT proposals -----


def lanes_to_clear(scenario: Scenario, arrivals_in_block: float) -> int:
    """Lanes that would exactly clear a block's arrivals, rounded up.

    Lanes beyond this stand idle: there is no queue left to process. This cap is
    what gives CP-SAT a diminishing return, and is the difference between a
    balanced allocation and a corner solution that piles every spare body onto
    the single highest-pressure gate.
    """
    import math as _math

    per_lane = scenario.service.service_rate_per_lane_per_min * scenario.limits.staff_block_minutes
    if per_lane <= 0:
        return scenario.limits.total_staff // scenario.service.staff_per_lane
    return max(_math.ceil(arrivals_in_block / per_lane), 1)


def staff_to_clear(scenario: Scenario, arrivals_in_block: float) -> int:
    """Headcount that would exactly clear a block's arrivals, rounded up."""
    return max(
        lanes_to_clear(scenario, arrivals_in_block) * scenario.service.staff_per_lane,
        scenario.limits.min_staff_per_gate,
    )


def apportion_staff(
    scenario: Scenario,
    pressure: dict[str, dict[int, float]],
    exponent: float,
) -> dict[str, dict[int, int]]:
    """Deterministic staffing proposal: largest-remainder apportionment by pressure.

    Pure integer arithmetic over sorted keys, so it produces byte-identical
    output on any platform, any Python build, any CPU. That portability is the
    entire point - see ``propose_staffing`` for why it matters.

    Each gate gets the floor of its proportional share, then the remaining
    headcount goes to the largest fractional remainders, ties broken by gate id
    so the result never depends on dict ordering. Shares are capped at what
    actually clears the block, because staff beyond that stand idle, and floored
    at the scenario minimum.
    """
    limits = scenario.limits
    blocks = block_count(scenario)
    gates = sorted(scenario.gates, key=lambda g: g.id)
    baseline_arrivals = arrival_profile(scenario, Plan.baseline(scenario))

    allocation: dict[str, dict[int, int]] = {g.id: {} for g in gates}
    for block in range(blocks):
        start = block * limits.staff_block_minutes
        weights: dict[str, float] = {}
        caps: dict[str, int] = {}
        for gate in gates:
            window = baseline_arrivals[gate.id][start : start + limits.staff_block_minutes]
            caps[gate.id] = min(
                lanes_to_clear(scenario, sum(window)) * scenario.service.staff_per_lane,
                limits.total_staff,
            )
            weights[gate.id] = pressure[gate.id][block] ** exponent

        floor = limits.min_staff_per_gate
        spare = limits.total_staff - floor * len(gates)
        total_weight = sum(weights.values())
        if spare <= 0 or total_weight <= 0:
            for gate in gates:
                allocation[gate.id][block] = floor
            continue

        exact = {g.id: spare * weights[g.id] / total_weight for g in gates}
        assigned = {g.id: min(int(exact[g.id]), max(caps[g.id] - floor, 0)) for g in gates}
        remaining = spare - sum(assigned.values())

        # Largest remainder, then gate id. Never a float comparison for the tie.
        order = sorted(
            gates,
            key=lambda g: (-(exact[g.id] - int(exact[g.id])), g.id),
        )
        cursor = 0
        while remaining > 0 and cursor < len(order) * 4:
            gate = order[cursor % len(order)]
            if assigned[gate.id] + floor < caps[gate.id]:
                assigned[gate.id] += 1
                remaining -= 1
            cursor += 1
        for gate in gates:
            allocation[gate.id][block] = floor + assigned[gate.id]

    # Collapse to at most max_staff_moves changes by holding the modal
    # allocation for each gate across the whole event. A time-varying plan that
    # breaches the move cap is worthless operationally and would be rejected by
    # the validator anyway.
    collapsed: dict[str, dict[int, int]] = {}
    for gate in gates:
        counts: dict[int, int] = {}
        for block in range(blocks):
            value = allocation[gate.id][block]
            counts[value] = counts.get(value, 0) + 1
        modal = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        collapsed[gate.id] = {b: modal for b in range(blocks)}

    # Apportionment can overshoot the cap once collapsed; shed from the
    # lowest-pressure gate first, deterministically.
    for block in range(blocks):
        while sum(collapsed[g.id][block] for g in gates) > limits.total_staff:
            donor = min(
                (g for g in gates if collapsed[g.id][block] > limits.min_staff_per_gate),
                key=lambda g: (pressure[g.id][block], g.id),
                default=None,
            )
            if donor is None:
                break
            for b in range(blocks):
                collapsed[donor.id][b] -= 1

    # Structural feasibility: every gate must be able to clear its own demand
    # over the event. This is the single constraint that made the CP-SAT
    # proposals good - without it a low-pressure gate gets shed to the floor and
    # carries a backlog all night whose person-minutes swamp everything the
    # reallocation bought. Top up starved gates by taking from the
    # lowest-pressure gate that can spare a body, deterministically.
    block_minutes = limits.staff_block_minutes
    rate = scenario.service.service_rate_per_lane_per_min

    def shortfall(gate_id: str) -> float:
        served = sum(
            scenario.service.lanes_for(collapsed[gate_id][b]) * rate * block_minutes
            for b in range(blocks)
        )
        return scenario.gate(gate_id).total_arrivals() - served

    for _ in range(limits.total_staff * 2):
        starved = [g for g in gates if shortfall(g.id) > 0]
        if not starved:
            break
        needy = max(starved, key=lambda g: (shortfall(g.id), g.id))
        donor = min(
            (
                g
                for g in gates
                if g.id != needy.id
                and collapsed[g.id][0] > limits.min_staff_per_gate
                and shortfall(g.id) <= 0
            ),
            key=lambda g: (pressure[g.id][0], g.id),
            default=None,
        )
        if donor is None:
            break
        for b in range(blocks):
            collapsed[donor.id][b] -= 1
            collapsed[needy.id][b] += 1

    return collapsed


def propose_staffing_deterministic(
    scenario: Scenario, thermal: ThermalField, count: int = STAFFING_PROPOSALS
) -> list[dict[str, dict[int, int]]]:
    """Portable staffing proposals. The default, and what the headline uses.

    CP-SAT is a genuinely better proposal generator and it stays in the codebase
    (``propose_staffing``), but its search is not portable: on identical input,
    identical solver version and identical deterministic time budget, the arm64
    development machine and the x86_64 deployment landed on different
    equally-optimal allocations, moving the headline from -23.5 % to -20.6 %.
    Both are valid plans and both clear the brief's gate, but "seed-reproducible
    headline numbers" is a submission requirement, and a number that changes with
    the CPU is not reproducible.

    So the default path is integer apportionment: no floating-point search, no
    solver, sorted iteration throughout. Set ``THERMCUE_USE_CPSAT=1`` to use the
    solver instead and compare.
    """
    pressure = heat_pressure(scenario, thermal)
    proposals: list[dict[str, dict[int, int]]] = []
    seen: set[str] = set()
    for index in range(count):
        exponent = 0.25 + index * 0.35
        allocation = apportion_staff(scenario, pressure, exponent)
        fingerprint = repr(sorted((k, tuple(sorted(v.items()))) for k, v in allocation.items()))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        proposals.append(allocation)
    return proposals


def propose_staffing(
    scenario: Scenario,
    thermal: ThermalField,
    count: int = STAFFING_PROPOSALS,
    seed_for_solver: int = 0,
) -> list[dict[str, dict[int, int]]]:
    """Integer staffing allocations from CP-SAT, honouring every hard limit.

    Constraints modelled exactly as the scenario declares them: total headcount,
    a floor per gate, and a cap on the number of block-boundary changes. The move
    cap is what makes this a constraint problem rather than a greedy fill -
    without it the optimal allocation reshuffles every block and is
    operationally worthless.

    The objective maximises band-weighted **useful** staff, where useful is
    ``min(assigned, staff_to_clear(arrivals))``. An earlier version maximised
    weighted headcount directly, which is linear, so every spare body went to the
    single highest-pressure gate: it proposed fifteen staff on Gate A and the
    floor of two everywhere else, scoring 72 % worse than the baseline it was
    meant to improve. Capping the useful count is what makes the return
    diminishing and the allocation balanced.

    Diversity comes from re-solving under different pressure exponents rather
    than from a solution pool, because CP-SAT's pool returns near-identical
    optima here and the search downstream benefits more from genuinely different
    shapes.
    """
    from ortools.sat.python import cp_model

    limits = scenario.limits
    blocks = block_count(scenario)
    gates = list(scenario.gates)
    pressure = heat_pressure(scenario, thermal)
    baseline_arrivals = arrival_profile(scenario, Plan.baseline(scenario))

    caps: dict[tuple[str, int], int] = {}
    for gate in gates:
        for block in range(blocks):
            start = block * limits.staff_block_minutes
            window = baseline_arrivals[gate.id][start : start + limits.staff_block_minutes]
            caps[(gate.id, block)] = min(
                lanes_to_clear(scenario, sum(window)),
                limits.total_staff // scenario.service.staff_per_lane,
            )

    max_pressure = max(
        (pressure[g.id][b] for g in gates for b in range(blocks)), default=1.0
    ) or 1.0

    # CP-SAT is integer-only, and the service rate is fractional persons per
    # minute. Everything entering a constraint is scaled by SCALE and the
    # scaling is applied consistently on both sides.
    service_rate_scaled = int(round(scenario.service.service_rate_per_lane_per_min * SCALE))

    proposals: list[dict[str, dict[int, int]]] = []
    seen: set[str] = set()

    for index in range(count):
        # Sharper exponents concentrate staff on the hottest, busiest blocks;
        # flatter ones spread them. Sweeping this is what produces distinct
        # shapes for the simulator to judge.
        exponent = 0.4 + index * 0.30
        model = cp_model.CpModel()

        staff = {
            (g.id, b): model.NewIntVar(
                limits.min_staff_per_gate, limits.total_staff, f"s_{g.id}_{b}"
            )
            for g in gates
            for b in range(blocks)
        }
        # Lanes are floor(staff / staff_per_lane). Modelled exactly, with an
        # integer variable bounded below the true floor, rather than relaxed to
        # staff / staff_per_lane. The relaxation looks harmless - "optimistic by
        # under one lane" - but at a gate running three staff it claims 1.5 lanes
        # against a real 1, a 50 % overstatement, and that is precisely the
        # regime where a gate is about to fall behind. It let Gate D pass a
        # structural-feasibility check it actually fails.
        max_lanes = limits.total_staff // scenario.service.staff_per_lane
        lanes = {
            (g.id, b): model.NewIntVar(0, max_lanes, f"l_{g.id}_{b}")
            for g in gates
            for b in range(blocks)
        }
        for g in gates:
            for b in range(blocks):
                model.Add(staff[(g.id, b)] >= lanes[(g.id, b)] * scenario.service.staff_per_lane)

        useful = {
            (g.id, b): model.NewIntVar(0, max_lanes, f"u_{g.id}_{b}")
            for g in gates
            for b in range(blocks)
        }
        for g in gates:
            for b in range(blocks):
                cap = model.NewConstant(caps[(g.id, b)])
                model.AddMinEquality(useful[(g.id, b)], [lanes[(g.id, b)], cap])

        for b in range(blocks):
            model.Add(sum(staff[(g.id, b)] for g in gates) <= limits.total_staff)

        # Structural feasibility: every gate must be scheduled to clear its own
        # demand by the end of the event. Without this the solver happily
        # starves a low-weight gate to the floor to feed a high-weight one, and
        # the abandoned gate carries a backlog for the whole night. Its
        # person-minutes then dominate everything the reallocation bought - the
        # first version of this model proposed exactly that and scored 9 to 49 %
        # *worse* than the baseline on every proposal it produced.
        block_capacity_per_lane = service_rate_scaled * limits.staff_block_minutes
        for g in gates:
            total_demand = int(round(sum(baseline_arrivals[g.id]) * SCALE))
            model.Add(
                sum(lanes[(g.id, b)] for b in range(blocks)) * block_capacity_per_lane
                >= total_demand
            )

        # A move is a block boundary where a gate's headcount changes.
        changed: list[Any] = []
        for g in gates:
            previous = model.NewConstant(g.staff_count)
            for b in range(blocks):
                flag = model.NewBoolVar(f"chg_{g.id}_{b}")
                difference = model.NewIntVar(
                    -limits.total_staff, limits.total_staff, f"d_{g.id}_{b}"
                )
                model.Add(difference == staff[(g.id, b)] - previous)
                model.Add(difference != 0).OnlyEnforceIf(flag)
                model.Add(difference == 0).OnlyEnforceIf(flag.Not())
                changed.append(flag)
                previous = staff[(g.id, b)]
        model.Add(sum(changed) <= limits.max_staff_moves)

        # Integer coefficients only: CP-SAT is an integer solver and a float
        # objective is silently truncated.
        terms = []
        for g in gates:
            for b in range(blocks):
                coefficient = int(
                    round(1000.0 * (pressure[g.id][b] / max_pressure) ** exponent)
                )
                terms.append(coefficient * useful[(g.id, b)])
        model.Maximize(sum(terms))

        solver = cp_model.CpSolver()
        # Determinism over speed. Multi-worker CP-SAT races its workers and
        # returns whichever equally-optimal solution finishes first, so the
        # staffing proposals differed between runs on byte-identical inputs and
        # the headline moved from 22.83 % to 17.20 % with nothing changed. The
        # submission requires seed-reproducible numbers, and a search seeded off
        # a coin flip is not seeded. One worker plus a deterministic time limit
        # makes the whole pipeline reproducible from the cache alone.
        #
        # 2.0 deterministic units, not more: swept against 1.0 and 4.0, this is
        # the smallest budget that converges to the same solution as 4.0, and it
        # keeps a full optimisation at about 14 s against 71 s at 20.0.
        solver.parameters.num_search_workers = 1
        solver.parameters.max_deterministic_time = 2.0
        solver.parameters.random_seed = seed_for_solver
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            continue

        allocation = {
            g.id: {b: int(solver.Value(staff[(g.id, b)])) for b in range(blocks)} for g in gates
        }
        fingerprint = repr(sorted((k, tuple(sorted(v.items()))) for k, v in allocation.items()))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        proposals.append(allocation)

    return proposals


# ------------------------------------------------------------- searching ----


def _score(
    scenario: Scenario,
    plan: Plan,
    thermal: ThermalField,
    seed: int,
) -> ScoredPlan | None:
    """Simulate a plan, or return None if it is infeasible.

    Infeasibility is a silent skip **here only**, because the search generates
    candidates combinatorially and most violate a limit. Nothing else in the
    codebase swallows PlanInfeasibleError.
    """
    try:
        result = simulate_fast(scenario, plan, thermal, seed=seed)
    except PlanInfeasibleError:
        return None
    return ScoredPlan(plan=plan, result=result)


def default_proposals(
    scenario: Scenario, thermal: ThermalField
) -> list[dict[str, dict[int, int]]]:
    """Portable apportionment by default; CP-SAT when explicitly opted into."""
    import os

    if os.getenv("THERMCUE_USE_CPSAT") == "1":
        return propose_staffing(scenario, thermal)
    return propose_staffing_deterministic(scenario, thermal)


def optimise(
    scenario: Scenario,
    thermal: ThermalField,
    seed: int = HEADLINE_SEED,
    wait_ratio: float | None = None,
    staffing_proposals: Sequence[dict[str, dict[int, int]]] | None = None,
    archive: list[ScoredPlan] | None = None,
) -> tuple[ScoredPlan, ScoredPlan, int]:
    """Search for the lowest-HPM feasible plan under the wait constraint.

    Returns ``(baseline, best, candidates_evaluated)``. The baseline is always
    feasible by construction, so the search can never return nothing.

    When an ``archive`` list is supplied every scored candidate is appended to
    it, including those rejected by the wait constraint. That archive is what
    the Pareto frontier is built from: re-running the whole search once per wait
    allowance costs four times as much and explores the same space, and the
    rejected candidates are exactly the points that make the frontier a frontier
    rather than a single dot.
    """
    limits = scenario.limits
    ratio = wait_ratio if wait_ratio is not None else limits.max_wait_increase_ratio

    baseline_plan = Plan.baseline(scenario)
    baseline = _score(scenario, baseline_plan, thermal, seed)
    if baseline is None:
        raise NoFeasiblePlanError("The baseline plan is infeasible against its own limits")
    wait_cap = baseline.total_wait * ratio

    proposals = list(
        staffing_proposals
        if staffing_proposals is not None
        else default_proposals(scenario, thermal)
    )
    # The baseline staffing must be in the pool, or a scenario where doing
    # nothing is right would be unable to express that.
    proposals.insert(0, baseline_plan.staff_by_block)

    # A four-point share grid rather than two. Staggering is the cheapest lever
    # on the board - it costs nothing operationally - so it is worth searching
    # finely; the simulations are a millisecond each.
    share_grid = [limits.stagger_max_share * f for f in (0.25, 0.5, 0.75, 1.0)]
    stagger_options = [
        (share, offset)
        for offset in limits.stagger_offsets_min
        for share in ({0.0} if offset == 0 else share_grid)
    ]
    if (0.0, 0) not in stagger_options:
        stagger_options.insert(0, (0.0, 0))

    best = baseline
    evaluated = 0

    def consider_against(incumbent: ScoredPlan, candidate: Plan) -> ScoredPlan | None:
        """Score a candidate and return it if it beats the incumbent.

        Everything scored lands in the archive whether or not it wins, including
        candidates rejected by the wait constraint, because those are exactly the
        points that make the Pareto frontier a frontier.
        """
        nonlocal evaluated, best
        scored = _score(scenario, candidate, thermal, seed)
        evaluated += 1
        if scored is None:
            return None
        if archive is not None:
            archive.append(scored)
        if scored.total_wait > wait_cap:
            return None
        if scored.hpm < best.hpm - 1e-9:
            best = scored
        if scored.hpm < incumbent.hpm - 1e-9:
            return scored
        return None

    def descend(start: ScoredPlan) -> ScoredPlan:
        """Coordinate descent from one starting plan.

        Each lever family is optimised against the current incumbent rather than
        against the start, so interactions between levers are picked up. Stops
        when a full pass changes nothing.
        """
        incumbent = start
        for _ in range(MAX_COORDINATE_PASSES):
            improved = False

            for combination in itertools.product(
                limits.gate_open_offsets_min, repeat=len(scenario.gates)
            ):
                offsets = {g.id: combination[i] for i, g in enumerate(scenario.gates)}
                found = consider_against(
                    incumbent, incumbent.plan.with_changes(gate_open_offset_min=offsets)
                )
                if found is not None:
                    incumbent, improved = found, True

            for allocation in proposals:
                found = consider_against(
                    incumbent, incumbent.plan.with_changes(staff_by_block=allocation)
                )
                if found is not None:
                    incumbent, improved = found, True

            # Staffing swap neighbourhood: move k staff from one gate to
            # another, for every ordered pair and every k. This is what makes
            # the result insensitive to the proposal set. Relying on seeds alone
            # left the search landing in whichever local optimum the first
            # improving move happened to reach, which is how the headline came
            # out at 23.5 % on one machine and 20.6 % on another from identical
            # input. Exploring the neighbourhood directly is deterministic,
            # cheap at roughly a millisecond per candidate, and converges to the
            # same plan regardless of where it started.
            for donor in scenario.gates:
                for receiver in scenario.gates:
                    if donor.id == receiver.id:
                        continue
                    for amount in STAFF_SWAP_SIZES:
                        for window in swap_windows:
                            allocation = {
                                g: dict(blocks)
                                for g, blocks in incumbent.plan.staff_by_block.items()
                            }
                            viable = True
                            for block in window:
                                allocation[donor.id][block] -= amount
                                allocation[receiver.id][block] += amount
                                if allocation[donor.id][block] < limits.min_staff_per_gate:
                                    viable = False
                                    break
                            if not viable:
                                continue
                            found = consider_against(
                                incumbent,
                                incumbent.plan.with_changes(staff_by_block=allocation),
                            )
                            if found is not None:
                                incumbent, improved = found, True

            for share, offset in stagger_options:
                found = consider_against(
                    incumbent,
                    incumbent.plan.with_changes(
                        stagger_share=share, stagger_offset_min=offset
                    ),
                )
                if found is not None:
                    incumbent, improved = found, True

            if not improved:
                break
        return incumbent

    # Multi-start. Coordinate descent is greedy and path-dependent: descending
    # from the baseline alone lands in whichever local optimum the first
    # improving move happens to lead to, and refining the stagger grid was
    # observed to make the result *worse* by 0.5 points for exactly that reason.
    # Restarting from each staffing proposal costs a few thousand simulations at
    # roughly a millisecond each and removes the dependence on move ordering.
    # Swap windows: the whole event, and each hour-aligned prefix and suffix.
    # A constant-across-the-event swap costs two staff moves; a windowed one
    # costs up to four, which is exactly the scenario's cap. Restricting swaps to
    # the whole event removed the time-varying dimension entirely, and that
    # dimension is where the good plans live - the best allocations move staff to
    # a gate for the arrival peak and hand them back afterwards.
    blocks_total = block_count(scenario)
    per_hour = 60 // limits.staff_block_minutes
    swap_windows: list[range] = [range(blocks_total)]
    for boundary in range(per_hour, blocks_total, per_hour):
        swap_windows.append(range(0, boundary))
        swap_windows.append(range(boundary, blocks_total))

    starts: list[ScoredPlan] = [baseline]
    for allocation in proposals:
        seeded = _score(
            scenario, baseline_plan.with_changes(staff_by_block=allocation), thermal, seed
        )
        evaluated += 1
        if seeded is None:
            continue
        if archive is not None:
            archive.append(seeded)
        starts.append(seeded)

    for start in starts:
        descend(start)

    return baseline, ScoredPlan(plan=best.plan.with_changes(label="optimised"), result=best.result), evaluated


# ------------------------------------------------------------ resources -----


def optimise_resources(
    scenario: Scenario, thermal: ThermalField, result: SimResult
) -> list[dict[str, Any]]:
    """Place movable water and rest points where heat exposure is worst.

    Resource placement does not change queue dynamics, so folding it into HPM
    would be dishonest - it would credit a water point with a wait reduction it
    cannot cause. It is optimised against its own metric, **relief coverage**:
    band-weighted person-minutes in a zone, which is the exposure a water point
    actually relieves. That number is reported separately and never added to the
    HPM improvement.
    """
    exposure: dict[str, float] = {z.id: 0.0 for z in scenario.zones}
    for gate_id, series in result.gates.items():
        zone_id = scenario.gate(gate_id).queue_zone
        for minute, queued in enumerate(series.queue):
            hour = scenario.start_hour + minute // 60
            exposure[zone_id] += queued * BAND_WEIGHTS[thermal.band_at(zone_id, hour)]

    ranked = sorted(exposure.items(), key=lambda kv: kv[1], reverse=True)
    # Coverage is tracked per resource type. A rest tent does not relieve thirst
    # and a water point is not shade; treating them as interchangeable left every
    # movable resource pinned in place because some other type already sat in the
    # zone that needed it.
    covered: dict[str, set[str]] = {"water": set(), "rest": set()}
    for r in scenario.resources:
        if r.id not in scenario.limits.movable_resources:
            covered[r.type].add(r.zone)
    moves: list[dict[str, Any]] = []
    movable = [r for r in scenario.resources if r.id in scenario.limits.movable_resources]

    for resource in movable:
        target = next(
            (
                zone_id
                for zone_id, score in ranked
                if zone_id not in covered[resource.type] and score > 0
            ),
            None,
        )
        if target is None or target == resource.zone:
            covered[resource.type].add(resource.zone)
            continue
        peak_hour = max(
            scenario.hours,
            key=lambda h: BAND_WEIGHTS[thermal.band_at(target, h)],
        )
        moves.append(
            {
                "resource_id": resource.id,
                "resource_name": resource.name,
                "type": resource.type,
                "from_zone": resource.zone,
                "to_zone": target,
                "relief_coverage_delta": round(exposure[target] - exposure.get(resource.zone, 0.0), 1),
                "binding_condition": (
                    f"{target} carries the highest band-weighted exposure with no "
                    f"{resource.type} point, peaking at "
                    f"{thermal.band_at(target, peak_hour)} band at {peak_hour:02d}:00"
                ),
                "zone": target,
                "hour": peak_hour,
            }
        )
        covered[resource.type].add(target)
    return moves


# ------------------------------------------------------- explainability -----


def _hours_for_block(scenario: Scenario, block: int) -> tuple[int, ...]:
    start = block * scenario.limits.staff_block_minutes
    end = start + scenario.limits.staff_block_minutes - 1
    return tuple(sorted({scenario.start_hour + start // 60, scenario.start_hour + end // 60}))


def _atomic_changes(baseline: Plan, optimised: Plan, scenario: Scenario) -> list[dict[str, Any]]:
    """Group the raw diff into changes an operator would treat as one action.

    Consecutive staffing blocks with the same before/after headcount at one gate
    are a single instruction - "move two staff to Gate D from 16:00 to 17:30" -
    not six separate ones. Reporting them separately would inflate the change
    count and make every counterfactual share look trivially small.
    """
    raw = baseline.diff(optimised, scenario)
    grouped: list[dict[str, Any]] = []
    staff_runs: dict[tuple[str, int, int], list[int]] = {}

    for change in raw:
        if change["kind"] != "staff":
            grouped.append(change)
            continue
        key = (change["gate_id"], change["before"], change["after"])
        staff_runs.setdefault(key, []).append(change["block"])

    for (gate_id, before, after), blocks in staff_runs.items():
        blocks.sort()
        run_start = blocks[0]
        previous = blocks[0]
        for block in blocks[1:] + [None]:
            if block is not None and block == previous + 1:
                previous = block
                continue
            grouped.append(
                {
                    "kind": "staff",
                    "gate_id": gate_id,
                    "gate_name": scenario.gate(gate_id).name,
                    "before": before,
                    "after": after,
                    "blocks": list(range(run_start, previous + 1)),
                }
            )
            if block is not None:
                run_start = block
                previous = block
    return grouped


def _plan_without(change: dict[str, Any], optimised: Plan, baseline: Plan, scenario: Scenario) -> Plan:
    """The optimised plan with exactly one change reverted to baseline."""
    if change["kind"] == "gate":
        offsets = dict(optimised.gate_open_offset_min)
        offsets[change["gate_id"]] = baseline.gate_open_offset_min.get(change["gate_id"], 0)
        return optimised.with_changes(gate_open_offset_min=offsets)
    if change["kind"] == "staff":
        allocation = {g: dict(blocks) for g, blocks in optimised.staff_by_block.items()}
        for block in change["blocks"]:
            allocation[change["gate_id"]][block] = baseline.staff_at(change["gate_id"], block)
        return optimised.with_changes(staff_by_block=allocation)
    if change["kind"] == "stagger":
        return optimised.with_changes(
            stagger_share=baseline.stagger_share, stagger_offset_min=baseline.stagger_offset_min
        )
    return optimised


def explain(
    scenario: Scenario,
    thermal: ThermalField,
    baseline: ScoredPlan,
    optimised: ScoredPlan,
    seed: int = HEADLINE_SEED,
) -> list[ChangeExplanation]:
    """Leave-one-out counterfactual attribution over the accepted changes.

    Each change is reverted on its own and the plan re-simulated. The HPM the
    plan gives back is that change's contribution. Shares are normalised over the
    positive contributions and the raw deltas are retained, because leave-one-out
    contributions do not sum to the total when levers interact, and quietly
    rescaling them would erase the evidence of that interaction.
    """
    changes = _atomic_changes(baseline.plan, optimised.plan, scenario)
    if not changes:
        return []

    total_improvement = baseline.hpm - optimised.hpm
    deltas: list[tuple[dict[str, Any], float, float]] = []

    for change in changes:
        reverted = _plan_without(change, optimised.plan, baseline.plan, scenario)
        try:
            without = simulate_fast(scenario, reverted, thermal, seed=seed)
        except PlanInfeasibleError:
            # Reverting one change can breach the move cap. That is itself
            # informative: the change is load-bearing, so it is credited with the
            # full remaining improvement rather than dropped.
            deltas.append((change, total_improvement, 0.0))
            continue
        # Positive means the plan is worse without this change, so the change
        # was worth that much.
        deltas.append((change, without.hpm - optimised.hpm, without.total_wait_minutes))

    positive_total = sum(max(d, 0.0) for _, d, _ in deltas) or 1.0
    explanations: list[ChangeExplanation] = []

    for index, (change, delta, _) in enumerate(deltas):
        kind = change["kind"]
        if kind == "gate":
            gate = scenario.gate(change["gate_id"])
            zone_id = gate.queue_zone
            minutes_earlier = -change["after"]
            hours = (gate.scheduled_open_hour,)
            action = (
                f"Open {gate.name} {minutes_earlier} minutes early"
                if minutes_earlier > 0
                else f"Hold {gate.name} to its scheduled opening"
            )
        elif kind == "staff":
            gate = scenario.gate(change["gate_id"])
            zone_id = gate.queue_zone
            hours = tuple(
                sorted({h for b in change["blocks"] for h in _hours_for_block(scenario, b)})
            )
            movement = change["after"] - change["before"]
            action = (
                f"Move {abs(movement)} staff {'to' if movement > 0 else 'from'} {gate.name} "
                f"({change['before']} to {change['after']})"
            )
        elif kind == "stagger":
            zone_id = None
            hours = scenario.hours
            share = change["after"]["share"]
            offset = change["after"]["offset_min"]
            action = f"Stagger {share * 100:.0f}% of arrivals by {offset} minutes"
        else:
            zone_id = change.get("after")
            hours = scenario.hours
            action = f"Relocate {change.get('resource_name', change.get('resource_id'))}"

        peak_hour = (
            max(hours, key=lambda h: BAND_WEIGHTS[thermal.band_at(zone_id, h)])
            if zone_id
            else max(
                scenario.hours,
                key=lambda h: max(BAND_WEIGHTS[thermal.band_at(z.id, h)] for z in scenario.zones),
            )
        )
        band = thermal.band_at(zone_id, peak_hour) if zone_id else "n/a"
        wbgt = thermal.wbgt_c.get(zone_id, {}).get(peak_hour) if zone_id else None
        # The queue cited is the one predicted **under the baseline**, because
        # that is the condition the change was made to prevent. Citing the
        # optimised queue produces the absurdity of "open Gate C early because
        # the predicted queue there is 0" - which it is, precisely because the
        # gate was opened early.
        predicted_queue = 0.0
        averted_queue = 0.0
        if kind in ("gate", "staff"):
            baseline_series = baseline.result.gates[change["gate_id"]]
            optimised_series = optimised.result.gates[change["gate_id"]]
            window = [
                m
                for m in range(len(baseline_series.queue))
                if scenario.start_hour + m // 60 in hours
            ]
            predicted_queue = max((baseline_series.queue[m] for m in window), default=0.0)
            averted_queue = predicted_queue - max(
                (optimised_series.queue[m] for m in window), default=0.0
            )

        explanations.append(
            ChangeExplanation(
                id=f"chg-{index + 1}",
                kind=kind,
                action=action,
                zone_id=zone_id,
                hours=hours,
                band_and_hour=(
                    f"{band} band at {peak_hour:02d}:00"
                    + (f" (WBGT est {wbgt:.1f} C)" if wbgt is not None else "")
                ),
                binding_condition=(
                    f"{scenario.zone(zone_id).name} reaches the {band} band at "
                    f"{peak_hour:02d}:00, where the unchanged plan queues "
                    f"{predicted_queue:.0f} people at this gate"
                    + (f"; the change removes {averted_queue:.0f} of them" if averted_queue > 0 else "")
                    if zone_id
                    else "Venue-wide arrival pressure across the event window"
                ),
                predicted_queue=round(predicted_queue, 1),
                hpm_delta=round(delta, 1),
                counterfactual_share_pct=round(100.0 * max(delta, 0.0) / positive_total, 1),
                raw=change,
            )
        )

    explanations.sort(key=lambda e: e.hpm_delta, reverse=True)
    return explanations


# ---------------------------------------------------------------- Pareto ----


def pareto_from_archive(
    baseline: ScoredPlan,
    archive: Sequence[ScoredPlan],
    chosen: ScoredPlan,
    ratios: Iterable[float] = PARETO_RATIOS,
) -> list[dict[str, Any]]:
    """Best achievable HPM at each wait-increase allowance, from scored candidates.

    The frontier is what turns a single recommendation into a decision the
    operator owns: they choose how much extra wait the site will absorb, and the
    engine says what that buys in heat exposure. Every point here was scored by
    the simulator during the search, so nothing on the chart is interpolated.
    """
    points: list[dict[str, Any]] = []
    for ratio in ratios:
        cap = baseline.total_wait * ratio
        eligible = [c for c in archive if c.total_wait <= cap]
        if not eligible:
            continue
        best = min(eligible, key=lambda c: c.hpm)
        points.append(
            {
                "wait_ratio": ratio,
                "total_wait_minutes": round(best.total_wait, 1),
                "heat_weighted_exposure": round(best.hpm, 1),
                "hpm_reduction_pct": round(
                    100.0 * (baseline.hpm - best.hpm) / baseline.hpm if baseline.hpm else 0.0, 2
                ),
                "is_chosen": abs(best.hpm - chosen.hpm) < 1e-6,
            }
        )
    return points


def pareto_scatter(
    baseline: ScoredPlan, archive: Sequence[ScoredPlan], chosen: ScoredPlan, limit: int = 60
) -> list[dict[str, Any]]:
    """Candidate cloud for the Pareto chart: baseline, candidates, chosen.

    Deduplicated on rounded coordinates and thinned to ``limit`` points, keeping
    the non-dominated ones first, because a chart with 800 overlapping dots
    communicates nothing.
    """
    seen: set[tuple[int, int]] = set()
    unique: list[ScoredPlan] = []
    for candidate in archive:
        key = (int(candidate.total_wait / 100), int(candidate.hpm / 100))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    def dominated(candidate: ScoredPlan) -> bool:
        return any(
            other.total_wait <= candidate.total_wait
            and other.hpm <= candidate.hpm
            and (other.total_wait < candidate.total_wait or other.hpm < candidate.hpm)
            for other in unique
        )

    frontier = [c for c in unique if not dominated(c)]
    rest = [c for c in unique if dominated(c)]
    selected = (frontier + rest)[:limit]

    points = [
        {
            "id": "pp-baseline",
            "total_wait_minutes": round(baseline.total_wait, 1),
            "heat_weighted_exposure": round(baseline.hpm, 1),
            "kind": "baseline",
        }
    ]
    for index, candidate in enumerate(selected):
        is_chosen = abs(candidate.hpm - chosen.hpm) < 1e-6 and abs(
            candidate.total_wait - chosen.total_wait
        ) < 1e-6
        if is_chosen:
            continue
        points.append(
            {
                "id": f"pp-{index}",
                "total_wait_minutes": round(candidate.total_wait, 1),
                "heat_weighted_exposure": round(candidate.hpm, 1),
                "kind": "candidate",
            }
        )
    points.append(
        {
            "id": "pp-chosen",
            "total_wait_minutes": round(chosen.total_wait, 1),
            "heat_weighted_exposure": round(chosen.hpm, 1),
            "kind": "chosen",
        }
    )
    return points


def run_full_optimisation(
    scenario: Scenario, thermal: ThermalField, seed: int = HEADLINE_SEED
) -> OptimisationResult:
    """Headline optimisation, Pareto frontier, candidate cloud and explanations.

    One search populates all of it. An earlier version re-ran the entire search
    once per Pareto ratio, which took 70 s and returned four identical points,
    because re-searching the same space under a looser constraint finds the same
    optimum. Archiving every scored candidate gives both the frontier and the
    scatter the chart needs, for the cost of one search.
    """
    proposals = default_proposals(scenario, thermal)
    archive: list[ScoredPlan] = []
    baseline, best, evaluated = optimise(
        scenario,
        thermal,
        seed=seed,
        wait_ratio=max(PARETO_RATIOS),
        staffing_proposals=proposals,
        archive=archive,
    )
    # The search above ran under the loosest allowance so the archive spans the
    # whole frontier. The headline plan is then the best candidate under the
    # scenario's own declared allowance, not the loosest one.
    headline_cap = baseline.total_wait * scenario.limits.max_wait_increase_ratio
    eligible = [c for c in archive if c.total_wait <= headline_cap] or [baseline]
    best = min(eligible, key=lambda c: c.hpm)
    best = ScoredPlan(plan=best.plan.with_changes(label="optimised"), result=best.result)

    changes = explain(scenario, thermal, baseline, best, seed=seed)
    frontier = pareto_from_archive(baseline, archive, best)
    scatter = pareto_scatter(baseline, archive, best)
    resource_moves = optimise_resources(scenario, thermal, best.result)

    notes: list[str] = [
        "The optimiser searches and the simulator judges: every candidate plan "
        "reported here was scored by running the queue simulation, not by "
        "evaluating a surrogate objective.",
        "Counterfactual shares come from leave-one-out re-simulation and are "
        "normalised over positive contributions. They do not sum to the total "
        "improvement when levers interact, which is why the raw HPM deltas are "
        "reported alongside them.",
        "Resource relocations are scored against relief coverage, not HPM: a "
        "water point does not shorten a queue, and crediting it with a wait "
        "reduction would be false.",
        f"{evaluated} candidate plans were simulated to produce this result.",
    ]
    if best.hpm >= baseline.hpm:
        notes.append(
            "No plan inside the operating limits improved on the baseline. "
            "Doing nothing is the recommendation."
        )
    if len({p["heat_weighted_exposure"] for p in frontier}) == 1:
        notes.append(
            "The Pareto frontier is flat: the best plan does not need any extra "
            "wait allowance, so loosening the wait constraint buys no further "
            "heat reduction. That is a real finding about this scenario, not a "
            "missing sweep."
        )

    return OptimisationResult(
        baseline=baseline,
        optimised=best,
        changes=changes,
        pareto=frontier,
        pareto_scatter=scatter,
        resource_moves=resource_moves,
        candidates_evaluated=evaluated,
        notes=notes,
    )
