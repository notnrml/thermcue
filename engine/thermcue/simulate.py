"""Queue simulation and the heat-weighted exposure metric.

Two simulators, deliberately
----------------------------
``simulate_fast`` is a one-minute fluid recursion over each gate: arrivals in,
capacity out, FIFO waiting time recovered from the cumulative arrival and served
curves. It runs a full event in well under a millisecond, which is what makes a
Monte Carlo of 200 replications and an optimiser that scores hundreds of
candidate plans possible inside a web request.

``simulate_simpy`` is a discrete-event model with one process per attendee and
lognormal service times, as the brief specifies. It is slower by three orders of
magnitude, so it is used for the headline reference run rather than the inner
loop.

The two are not alternatives to pick between: ``tests/test_simulate.py``
asserts they agree on the congested baseline, which is the regime this event
runs in. The fluid model's known weakness is light load, where stochastic
service creates queues a deterministic server never sees, so the agreement
tolerance is stated for the congested case and the divergence at low load is
documented rather than averaged away.

The metric
----------
Heat-weighted person-minutes, HPM::

    HPM = sum over minutes of queue_length[gate][minute] * band_weight[zone][hour]

with band weights 0 / 1 / 2 / 4 for Low / Moderate / High / Extreme. One person
queueing one minute in an Extreme-band zone costs four; the same minute in a Low
band costs nothing. This is the whole thesis of the product: total wait is not
the harm, wait in the heat is, and the two are separable.

Because the weights are a choice, ``weight_sensitivity`` reruns the headline
comparison under alternative weightings. If the ranking of plans flips when the
weights change, the metric is not defensible and the README must say so.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .models import WbgtBand
from .plan import Plan, block_for_minute
from .scenario import Scenario
from .thermal import BAND_WEIGHTS

#: Alternative weightings used by the sensitivity check. The middle entry is the
#: headline. Ranking stability across these is what makes the metric defensible.
WEIGHT_VARIANTS: dict[str, dict[WbgtBand, float]] = {
    "linear-0134": {"low": 0.0, "moderate": 1.0, "high": 3.0, "extreme": 4.0},
    "headline-0124": dict(BAND_WEIGHTS),
    "steep-0135": {"low": 0.0, "moderate": 1.0, "high": 3.0, "extreme": 5.0},
    "flat-0123": {"low": 0.0, "moderate": 1.0, "high": 2.0, "extreme": 3.0},
}

MONTE_CARLO_DEFAULT_N = 200
ARRIVAL_NOISE_SIGMA = 0.15
"""Lognormal multiplicative noise on hourly arrivals, roughly +/-15 %. Applied
per gate per hour, because a late train affects one gate, not the whole site."""

HEADLINE_SEED = 20260829
"""Fixed seed for the headline run. Every number quoted in the README, the video
and the exported action card must be reproducible from this seed."""


@dataclass(slots=True, frozen=True)
class ThermalField:
    """Per-zone, per-hour band and WBGT. The simulator's only thermal input."""

    band: dict[str, dict[int, WbgtBand]]
    wbgt_c: dict[str, dict[int, float]]

    def band_at(self, zone_id: str, hour: int) -> WbgtBand:
        return self.band.get(zone_id, {}).get(hour, "low")

    def weight_at(
        self, zone_id: str, hour: int, weights: dict[WbgtBand, float] | None = None
    ) -> float:
        table = weights or BAND_WEIGHTS
        return table[self.band_at(zone_id, hour)]


@dataclass(slots=True)
class GateSeries:
    """Per-minute series for one gate over the event window."""

    gate_id: str
    zone_id: str
    arrivals: list[float]
    served: list[float]
    queue: list[float]
    open_mask: list[bool]

    def person_minutes(self) -> float:
        """Total time spent queueing. Each minute, everyone in the queue waits
        one minute, so the sum of queue length over minutes is exactly the
        person-minutes of waiting."""
        return float(sum(self.queue))


@dataclass(slots=True)
class SimResult:
    """One simulation run. Everything the KPIs, charts and agent need."""

    gates: dict[str, GateSeries]
    start_hour: int
    hpm: float
    person_minutes_high_extreme: float
    total_wait_minutes: float
    longest_wait_minutes: float
    unserved_at_close: float
    seed: int
    engine: str

    def queue_at(self, gate_id: str, minute: int) -> float:
        return self.gates[gate_id].queue[minute]

    def hourly_rows(self) -> list[dict]:
        """One row per gate per hour, matching the UI's QueueState contract."""
        rows: list[dict] = []
        for gate_id, series in self.gates.items():
            total_minutes = len(series.queue)
            for offset in range(0, total_minutes, 60):
                window = slice(offset, min(offset + 60, total_minutes))
                arrivals = sum(series.arrivals[window])
                queue_minutes = sum(series.queue[window])
                served = sum(series.served[window])
                window_minutes = max(1, min(offset + 60, total_minutes) - offset)
                # Mean wait experienced by those served in this hour, by Little's
                # law over the hour. Reported as zero when nobody was served,
                # rather than dividing by zero and rendering NaN in a chart.
                mean_wait = queue_minutes / served if served > 0 else 0.0
                rows.append(
                    {
                        "gate_id": gate_id,
                        "hour": self.start_hour + offset // 60,
                        "arrivals": int(round(arrivals)),
                        # Queue length is a time average, not person-minutes
                        # divided by experienced wait. Keeping it explicit
                        # prevents the UI from displaying the number served as
                        # though it were the number waiting.
                        "queue_length": round(queue_minutes / window_minutes, 1),
                        "wait_time_minutes": round(mean_wait, 2),
                        "person_minutes": round(queue_minutes, 1),
                    }
                )
        return rows


# ----------------------------------------------------------------- inputs ---


def arrival_profile(
    scenario: Scenario,
    plan: Plan,
    rng: random.Random | None = None,
    noise_sigma: float = 0.0,
) -> dict[str, list[float]]:
    """Per-minute arrival rate per gate, after stagger and optional noise.

    The hourly curve is spread evenly within the hour. That is a modelling
    choice, not a measurement, and it matters: a spikier within-hour shape would
    produce larger transient queues. It is stated in the README limitations.

    Staggering moves ``stagger_share`` of every gate's arrivals later by
    ``stagger_offset_min``. Arrivals pushed past the end of the window are held
    at the final minute rather than discarded, because people who were told to
    come later still turn up; silently dropping them would manufacture a wait
    reduction out of nothing.
    """
    minutes = scenario.duration_minutes
    profiles: dict[str, list[float]] = {}
    for gate in scenario.gates:
        base = [0.0] * minutes
        for hour_index, hour in enumerate(scenario.hours):
            count = float(gate.arrivals_by_hour[hour])
            if noise_sigma > 0.0 and rng is not None:
                # Lognormal keeps the multiplier strictly positive; the mean is
                # corrected so noise does not quietly inflate total attendance.
                multiplier = math.exp(rng.gauss(0.0, noise_sigma) - 0.5 * noise_sigma**2)
                count *= multiplier
            per_minute = count / 60.0
            for offset in range(60):
                base[hour_index * 60 + offset] = per_minute

        if plan.stagger_share > 0.0 and plan.stagger_offset_min > 0:
            shifted = [0.0] * minutes
            for minute, rate in enumerate(base):
                stay = rate * (1.0 - plan.stagger_share)
                move = rate * plan.stagger_share
                shifted[minute] += stay
                target = min(minute + plan.stagger_offset_min, minutes - 1)
                shifted[target] += move
            base = shifted
        profiles[gate.id] = base
    return profiles


def capacity_profile(scenario: Scenario, plan: Plan) -> dict[str, list[float]]:
    """Per-minute service capacity per gate, after opening offsets and staffing.

    A gate opens at ``scheduled_open_hour`` shifted by its plan offset, which is
    negative for opening earlier. Opening before the event window starts is
    clamped to the window: the simulation has no minutes before minute zero, and
    silently extending the window would let the optimiser buy capacity by
    inventing time.
    """
    minutes = scenario.duration_minutes
    service = scenario.service
    profiles: dict[str, list[float]] = {}
    for gate in scenario.gates:
        offset_min = plan.gate_open_offset_min.get(gate.id, 0)
        scheduled_minute = (gate.scheduled_open_hour - scenario.start_hour) * 60
        open_minute = max(scheduled_minute + offset_min, 0)
        row = [0.0] * minutes
        for minute in range(minutes):
            if minute < open_minute:
                continue
            staff = plan.staff_at(gate.id, block_for_minute(scenario, minute))
            lanes = service.lanes_for(staff)
            row[minute] = lanes * service.service_rate_per_lane_per_min
        profiles[gate.id] = row
    return profiles


# ------------------------------------------------------------ fast engine ---


def longest_fifo_wait(arrivals: Sequence[float], served: Sequence[float]) -> float:
    """Longest individual wait, from the cumulative arrival and served curves.

    Under FIFO the person who leaves at minute ``t`` arrived at the minute when
    the cumulative arrival curve first reached the cumulative served count at
    ``t``. The horizontal gap between the two curves is that person's wait; the
    vertical gap is the queue length. Total waiting is the area between them,
    which is exactly the sum of queue lengths and is accumulated by the caller,
    so only the maximum is computed here.
    """
    cumulative_arrivals: list[float] = []
    running = 0.0
    for value in arrivals:
        running += value
        cumulative_arrivals.append(running)

    longest = 0.0
    served_running = 0.0
    arrival_cursor = 0
    for minute, count in enumerate(served):
        served_running += count
        if count <= 0:
            continue
        while (
            arrival_cursor < len(cumulative_arrivals) - 1
            and cumulative_arrivals[arrival_cursor] < served_running
        ):
            arrival_cursor += 1
        longest = max(longest, float(minute - arrival_cursor))
    return longest


def simulate_fast(
    scenario: Scenario,
    plan: Plan,
    thermal: ThermalField,
    seed: int = HEADLINE_SEED,
    noise_sigma: float = 0.0,
    weights: dict[WbgtBand, float] | None = None,
) -> SimResult:
    """One-minute fluid queue recursion. The optimiser's and Monte Carlo's engine.

    ``q[t] = max(0, q[t-1] + a[t] - c[t])`` with ``served[t]`` the amount
    actually drained. Deterministic given the seed: the only randomness is the
    arrival noise, drawn from a seeded generator before the recursion starts.
    """
    plan.validate_against(scenario)
    rng = random.Random(seed)
    arrivals = arrival_profile(scenario, plan, rng, noise_sigma)
    capacity = capacity_profile(scenario, plan)
    minutes = scenario.duration_minutes

    gates: dict[str, GateSeries] = {}
    hpm = 0.0
    person_minutes_high_extreme = 0.0
    total_wait = 0.0
    longest_wait = 0.0
    unserved = 0.0

    for gate in scenario.gates:
        arrival_row = arrivals[gate.id]
        capacity_row = capacity[gate.id]
        queue_row: list[float] = []
        served_row: list[float] = []
        queue = 0.0
        for minute in range(minutes):
            waiting = queue + arrival_row[minute]
            drained = min(waiting, capacity_row[minute])
            queue = waiting - drained
            served_row.append(drained)
            queue_row.append(queue)

        series = GateSeries(
            gate_id=gate.id,
            zone_id=gate.queue_zone,
            arrivals=arrival_row,
            served=served_row,
            queue=queue_row,
            open_mask=[c > 0 for c in capacity_row],
        )
        gates[gate.id] = series

        for minute, queued in enumerate(queue_row):
            hour = scenario.start_hour + minute // 60
            band = thermal.band_at(gate.queue_zone, hour)
            hpm += queued * (weights or BAND_WEIGHTS)[band]
            if band in ("high", "extreme"):
                person_minutes_high_extreme += queued
        total_wait += series.person_minutes()
        longest_wait = max(longest_wait, longest_fifo_wait(arrival_row, served_row))
        unserved += queue_row[-1]

    return SimResult(
        gates=gates,
        start_hour=scenario.start_hour,
        hpm=hpm,
        person_minutes_high_extreme=person_minutes_high_extreme,
        total_wait_minutes=total_wait,
        longest_wait_minutes=longest_wait,
        unserved_at_close=unserved,
        seed=seed,
        engine="fluid-1min",
    )


# ----------------------------------------------------------- SimPy engine ---


def simulate_simpy(
    scenario: Scenario,
    plan: Plan,
    thermal: ThermalField,
    seed: int = HEADLINE_SEED,
    noise_sigma: float = 0.0,
    weights: dict[WbgtBand, float] | None = None,
) -> SimResult:
    """Discrete-event reference model: one process per attendee, lognormal service.

    Slower than the fluid model by roughly three orders of magnitude, so it backs
    the headline run rather than the optimiser loop. Its purpose is to keep the
    fast model honest: service time here is lognormal with the scenario's
    coefficient of variation, and lanes are real SimPy resources, so queueing
    arises from contention rather than from an equation.
    """
    import simpy

    plan.validate_against(scenario)
    rng = random.Random(seed)
    arrivals = arrival_profile(scenario, plan, rng, noise_sigma)
    capacity = capacity_profile(scenario, plan)
    service = scenario.service
    minutes = scenario.duration_minutes

    env = simpy.Environment()
    queue_series: dict[str, list[float]] = {g.id: [0.0] * minutes for g in scenario.gates}
    served_series: dict[str, list[float]] = {g.id: [0.0] * minutes for g in scenario.gates}
    waiting: dict[str, int] = {g.id: 0 for g in scenario.gates}
    waits: dict[str, list[float]] = {g.id: [] for g in scenario.gates}

    # One SimPy resource per gate, with capacity equal to the maximum lane count
    # the plan ever gives it. Lanes that are not staffed in a given block are
    # withheld by the gatekeeper process below rather than by resizing the
    # resource, which SimPy does not support mid-run.
    max_lanes = {
        g.id: max(
            service.lanes_for(plan.staff_at(g.id, b))
            for b in range(len(plan.staff_by_block.get(g.id, {0: 0})))
        )
        or 1
        for g in scenario.gates
    }
    lanes = {g.id: simpy.Resource(env, capacity=max_lanes[g.id]) for g in scenario.gates}

    def lanes_open(gate_id: str, minute: int) -> int:
        return int(capacity[gate_id][minute] / service.service_rate_per_lane_per_min)

    def attendee(gate_id: str) -> Iterable:
        arrived = env.now
        waiting[gate_id] += 1
        with lanes[gate_id].request() as request:
            yield request
            # Hold outside the open window or beyond the staffed lane count.
            while True:
                minute = min(int(env.now), minutes - 1)
                open_lanes = lanes_open(gate_id, minute)
                if open_lanes >= lanes[gate_id].count:
                    break
                yield env.timeout(1.0)
            waiting[gate_id] -= 1
            waits[gate_id].append(env.now - arrived)
            index = min(int(env.now), minutes - 1)
            served_series[gate_id][index] += 1.0
            mean_service = 1.0 / service.service_rate_per_lane_per_min
            cv = service.service_time_cv
            if cv > 0:
                sigma = math.sqrt(math.log(1.0 + cv**2))
                mu = math.log(mean_service) - 0.5 * sigma**2
                duration = rng.lognormvariate(mu, sigma)
            else:
                duration = mean_service
            yield env.timeout(duration)

    def spawner() -> Iterable:
        for minute in range(minutes):
            for gate in scenario.gates:
                # Fractional arrivals are resolved stochastically so the expected
                # count matches the profile exactly rather than being truncated.
                rate = arrivals[gate.id][minute]
                count = int(rate) + (1 if rng.random() < (rate - int(rate)) else 0)
                for _ in range(count):
                    env.process(attendee(gate.id))
            yield env.timeout(1.0)

    def sampler() -> Iterable:
        for minute in range(minutes):
            for gate in scenario.gates:
                queue_series[gate.id][minute] = float(waiting[gate.id])
            yield env.timeout(1.0)

    env.process(spawner())
    env.process(sampler())
    env.run(until=minutes)

    gates: dict[str, GateSeries] = {}
    hpm = 0.0
    person_minutes_high_extreme = 0.0
    total_wait = 0.0
    longest_wait = 0.0
    unserved = 0.0
    for gate in scenario.gates:
        series = GateSeries(
            gate_id=gate.id,
            zone_id=gate.queue_zone,
            arrivals=arrivals[gate.id],
            served=served_series[gate.id],
            queue=queue_series[gate.id],
            open_mask=[c > 0 for c in capacity[gate.id]],
        )
        gates[gate.id] = series
        for minute, queued in enumerate(series.queue):
            hour = scenario.start_hour + minute // 60
            band = thermal.band_at(gate.queue_zone, hour)
            hpm += queued * (weights or BAND_WEIGHTS)[band]
            if band in ("high", "extreme"):
                person_minutes_high_extreme += queued
        total_wait += series.person_minutes()
        longest_wait = max(longest_wait, max(waits[gate.id], default=0.0))
        unserved += series.queue[-1]

    return SimResult(
        gates=gates,
        start_hour=scenario.start_hour,
        hpm=hpm,
        person_minutes_high_extreme=person_minutes_high_extreme,
        total_wait_minutes=total_wait,
        longest_wait_minutes=longest_wait,
        unserved_at_close=unserved,
        seed=seed,
        engine="simpy-des",
    )


# ---------------------------------------------------------- Monte Carlo -----


@dataclass(slots=True)
class MonteCarloResult:
    n: int
    seed: int
    hpm_p10: float
    hpm_p50: float
    hpm_p90: float
    wait_p10: float
    wait_p50: float
    wait_p90: float
    hpm_samples: list[float] = field(repr=False, default_factory=list)
    wait_samples: list[float] = field(repr=False, default_factory=list)


def _percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile. numpy is a dependency but this keeps the
    metric definition visible in the same file as the metric."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def monte_carlo(
    scenario: Scenario,
    plan: Plan,
    thermal: ThermalField,
    n: int = MONTE_CARLO_DEFAULT_N,
    seed: int = HEADLINE_SEED,
    noise_sigma: float = ARRIVAL_NOISE_SIGMA,
    simulator: Callable[..., SimResult] = simulate_fast,
) -> MonteCarloResult:
    """Replicate under arrival noise and report the P10/P50/P90 envelope.

    Replication seeds are derived deterministically from ``seed``, so the whole
    envelope is reproducible from one number. A failure that cannot be replayed
    from a seed is a failure nobody can debug.
    """
    hpm_samples: list[float] = []
    wait_samples: list[float] = []
    for replication in range(n):
        result = simulator(
            scenario, plan, thermal, seed=seed + replication, noise_sigma=noise_sigma
        )
        hpm_samples.append(result.hpm)
        wait_samples.append(result.total_wait_minutes)
    return MonteCarloResult(
        n=n,
        seed=seed,
        hpm_p10=_percentile(hpm_samples, 0.10),
        hpm_p50=_percentile(hpm_samples, 0.50),
        hpm_p90=_percentile(hpm_samples, 0.90),
        wait_p10=_percentile(wait_samples, 0.10),
        wait_p50=_percentile(wait_samples, 0.50),
        wait_p90=_percentile(wait_samples, 0.90),
        hpm_samples=hpm_samples,
        wait_samples=wait_samples,
    )


def weight_sensitivity(
    scenario: Scenario,
    baseline: Plan,
    optimised: Plan,
    thermal: ThermalField,
    seed: int = HEADLINE_SEED,
) -> dict[str, dict[str, float]]:
    """Rerun the headline comparison under every band weighting variant.

    If the optimised plan stops beating the baseline under a plausible
    alternative weighting, the improvement is an artefact of the weights rather
    than of the plan, and the README must say so. This function is the evidence
    for that claim either way.
    """
    out: dict[str, dict[str, float]] = {}
    for name, weights in WEIGHT_VARIANTS.items():
        base = simulate_fast(scenario, baseline, thermal, seed=seed, weights=weights)
        opt = simulate_fast(scenario, optimised, thermal, seed=seed, weights=weights)
        reduction = (base.hpm - opt.hpm) / base.hpm if base.hpm > 0 else 0.0
        out[name] = {
            "baseline_hpm": round(base.hpm, 1),
            "optimised_hpm": round(opt.hpm, 1),
            "hpm_reduction_pct": round(reduction * 100.0, 2),
            "optimised_wins": opt.hpm < base.hpm,
        }
    return out
