"""Queue simulation tests.

The brief names three: conservation, seed determinism, and a monotonic response
to doubling arrivals. All three are here, plus the cross-engine agreement that
justifies running a fluid model in the optimiser loop at all.
"""

from __future__ import annotations

import pytest

from thermcue.plan import Plan, PlanInfeasibleError
from thermcue.simulate import (
    HEADLINE_SEED,
    ThermalField,
    arrival_profile,
    capacity_profile,
    longest_fifo_wait,
    monte_carlo,
    simulate_fast,
    simulate_simpy,
    weight_sensitivity,
)
from thermcue.thermal import BAND_WEIGHTS


class TestConservation:
    def test_arrivals_equal_served_plus_queued(self, scenario, baseline_plan, thermal):
        """Nobody is created and nobody vanishes. The single most important
        invariant: a leak here would silently improve every metric."""
        result = simulate_fast(scenario, baseline_plan, thermal)
        for gate_id, series in result.gates.items():
            arrived = sum(series.arrivals)
            served = sum(series.served)
            still_queued = series.queue[-1]
            assert served + still_queued == pytest.approx(arrived, rel=1e-9), gate_id

    def test_arrival_profile_preserves_the_scenario_total(self, scenario, baseline_plan):
        profiles = arrival_profile(scenario, baseline_plan)
        for gate in scenario.gates:
            assert sum(profiles[gate.id]) == pytest.approx(gate.total_arrivals(), rel=1e-9)

    def test_staggering_preserves_the_total(self, scenario, baseline_plan):
        """Staggering moves people later; it must not lose them. Dropping the
        overflow past the window would manufacture a wait reduction from
        nothing."""
        staggered = baseline_plan.with_changes(stagger_share=0.2, stagger_offset_min=30)
        profiles = arrival_profile(scenario, staggered)
        for gate in scenario.gates:
            assert sum(profiles[gate.id]) == pytest.approx(gate.total_arrivals(), rel=1e-9)

    def test_queue_is_never_negative(self, scenario, baseline_plan, thermal):
        result = simulate_fast(scenario, baseline_plan, thermal)
        for series in result.gates.values():
            assert min(series.queue) >= 0.0


class TestDeterminism:
    def test_same_seed_gives_identical_results(self, scenario, baseline_plan, thermal):
        a = simulate_fast(scenario, baseline_plan, thermal, seed=7, noise_sigma=0.15)
        b = simulate_fast(scenario, baseline_plan, thermal, seed=7, noise_sigma=0.15)
        assert a.hpm == b.hpm
        assert a.total_wait_minutes == b.total_wait_minutes

    def test_different_seeds_diverge_under_noise(self, scenario, baseline_plan, thermal):
        a = simulate_fast(scenario, baseline_plan, thermal, seed=7, noise_sigma=0.15)
        b = simulate_fast(scenario, baseline_plan, thermal, seed=8, noise_sigma=0.15)
        assert a.hpm != b.hpm

    def test_zero_noise_ignores_the_seed(self, scenario, baseline_plan, thermal):
        a = simulate_fast(scenario, baseline_plan, thermal, seed=7, noise_sigma=0.0)
        b = simulate_fast(scenario, baseline_plan, thermal, seed=999, noise_sigma=0.0)
        assert a.hpm == b.hpm

    def test_monte_carlo_envelope_is_reproducible(self, scenario, baseline_plan, thermal):
        a = monte_carlo(scenario, baseline_plan, thermal, n=25, seed=HEADLINE_SEED)
        b = monte_carlo(scenario, baseline_plan, thermal, n=25, seed=HEADLINE_SEED)
        assert (a.hpm_p10, a.hpm_p50, a.hpm_p90) == (b.hpm_p10, b.hpm_p50, b.hpm_p90)


class TestMonotonicity:
    def test_doubling_arrivals_does_not_reduce_exposure(self, scenario, baseline_plan, thermal):
        """The brief's monotonicity check. Twice the people cannot mean less
        queueing."""
        import copy

        from thermcue.scenario import GateSpec

        doubled = copy.deepcopy(scenario)
        gates = tuple(
            GateSpec(
                id=g.id,
                name=g.name,
                coordinates=g.coordinates,
                queue_zone=g.queue_zone,
                staff_count=g.staff_count,
                scheduled_open_hour=g.scheduled_open_hour,
                arrivals_by_hour={h: v * 2 for h, v in g.arrivals_by_hour.items()},
            )
            for g in scenario.gates
        )
        object.__setattr__(doubled, "gates", gates)

        single = simulate_fast(scenario, Plan.baseline(scenario), thermal)
        double = simulate_fast(doubled, Plan.baseline(doubled), thermal)
        assert double.hpm > single.hpm
        assert double.total_wait_minutes > single.total_wait_minutes

    def test_more_staff_never_increases_exposure(self, scenario, thermal):
        """Extra capacity cannot make queues worse. Verified against a relaxed
        scenario, because the real one is already at its staffing cap."""
        import copy

        from thermcue.scenario import LimitsSpec

        relaxed = copy.deepcopy(scenario)
        limits = scenario.limits
        object.__setattr__(
            relaxed,
            "limits",
            LimitsSpec(
                gate_open_offsets_min=limits.gate_open_offsets_min,
                total_staff=limits.total_staff * 2,
                min_staff_per_gate=limits.min_staff_per_gate,
                max_staff_moves=99,
                staff_block_minutes=limits.staff_block_minutes,
                stagger_max_share=limits.stagger_max_share,
                stagger_offsets_min=limits.stagger_offsets_min,
                movable_resources=limits.movable_resources,
                max_wait_increase_ratio=limits.max_wait_increase_ratio,
            ),
        )
        base = Plan.baseline(relaxed)
        richer = base.with_changes(
            staff_by_block={
                g.id: {b: v + 2 for b, v in blocks.items()}
                for g, blocks in ((g, base.staff_by_block[g.id]) for g in relaxed.gates)
            }
        )
        lean = simulate_fast(relaxed, base, thermal)
        rich = simulate_fast(relaxed, richer, thermal)
        assert rich.hpm <= lean.hpm
        assert rich.total_wait_minutes <= lean.total_wait_minutes

    def test_opening_a_gate_earlier_never_increases_its_queue(
        self, scenario, baseline_plan, thermal
    ):
        early = baseline_plan.with_changes(
            gate_open_offset_min={g.id: -45 for g in scenario.gates}
        )
        base = simulate_fast(scenario, baseline_plan, thermal)
        opened = simulate_fast(scenario, early, thermal)
        assert opened.total_wait_minutes <= base.total_wait_minutes


class TestCapacity:
    def test_a_closed_gate_has_no_capacity(self, scenario, thermal):
        plan = Plan.baseline(scenario)
        capacity = capacity_profile(scenario, plan)
        gate_c = scenario.gate("g-c")
        closed_until = (gate_c.scheduled_open_hour - scenario.start_hour) * 60
        assert all(c == 0.0 for c in capacity["g-c"][:closed_until])
        assert capacity["g-c"][closed_until] > 0.0

    def test_opening_offset_cannot_reach_before_the_window(self, scenario):
        """A gate scheduled for the first hour and offset by 45 minutes cannot
        open at minute -45; the window has no such minute. Silently extending it
        would let the optimiser buy capacity by inventing time."""
        plan = Plan.baseline(scenario).with_changes(
            gate_open_offset_min={g.id: -45 for g in scenario.gates}
        )
        capacity = capacity_profile(scenario, plan)
        assert capacity["g-a"][0] > 0.0
        assert len(capacity["g-a"]) == scenario.duration_minutes


class TestMetric:
    def test_hpm_is_zero_when_every_band_is_low(self, scenario, baseline_plan):
        cool = ThermalField(
            band={z.id: {h: "low" for h in scenario.hours} for z in scenario.zones},
            wbgt_c={z.id: {h: 20.0 for h in scenario.hours} for z in scenario.zones},
        )
        result = simulate_fast(scenario, baseline_plan, cool)
        assert result.hpm == pytest.approx(0.0)
        # Total wait is unchanged: heat weighting must not touch the queue model.
        assert result.total_wait_minutes > 0.0

    def test_hpm_scales_with_band_weight(self, scenario, baseline_plan):
        moderate = ThermalField(
            band={z.id: {h: "moderate" for h in scenario.hours} for z in scenario.zones},
            wbgt_c={z.id: {h: 28.0 for h in scenario.hours} for z in scenario.zones},
        )
        extreme = ThermalField(
            band={z.id: {h: "extreme" for h in scenario.hours} for z in scenario.zones},
            wbgt_c={z.id: {h: 33.0 for h in scenario.hours} for z in scenario.zones},
        )
        a = simulate_fast(scenario, baseline_plan, moderate)
        b = simulate_fast(scenario, baseline_plan, extreme)
        ratio = BAND_WEIGHTS["extreme"] / BAND_WEIGHTS["moderate"]
        assert b.hpm == pytest.approx(a.hpm * ratio, rel=1e-9)

    def test_person_minutes_equals_queue_length_summed_over_minutes(
        self, scenario, baseline_plan, thermal
    ):
        result = simulate_fast(scenario, baseline_plan, thermal)
        for series in result.gates.values():
            assert series.person_minutes() == pytest.approx(sum(series.queue))

    def test_hourly_rows_cover_every_gate_and_hour(self, scenario, baseline_plan, thermal):
        rows = simulate_fast(scenario, baseline_plan, thermal).hourly_rows()
        assert len(rows) == len(scenario.gates) * len(scenario.hours)
        assert {r["hour"] for r in rows} == set(scenario.hours)
        for row in rows:
            assert row["queue_length"] == pytest.approx(
                row["person_minutes"] / 60.0, abs=0.06
            )

    def test_hourly_wait_is_zero_rather_than_nan_when_nobody_is_served(
        self, scenario, thermal
    ):
        """Gate C is shut for the first hour. A naive mean would divide by zero
        and render NaN into a chart."""
        rows = simulate_fast(scenario, Plan.baseline(scenario), thermal).hourly_rows()
        first = [r for r in rows if r["gate_id"] == "g-c" and r["hour"] == 15][0]
        assert first["wait_time_minutes"] == 0.0


class TestEngineAgreement:
    def test_fluid_and_discrete_event_engines_agree_when_congested(
        self, scenario, baseline_plan, thermal
    ):
        """The fluid model is only used in the optimiser loop because it agrees
        with the discrete-event model in this regime. If that stops being true,
        every optimiser number is suspect and this test must fail loudly rather
        than the tolerance being widened."""
        fast = simulate_fast(scenario, baseline_plan, thermal)
        des = simulate_simpy(scenario, baseline_plan, thermal)
        assert des.hpm == pytest.approx(fast.hpm, rel=0.10)
        assert des.total_wait_minutes == pytest.approx(fast.total_wait_minutes, rel=0.10)

    def test_both_engines_conserve_people(self, scenario, baseline_plan, thermal):
        des = simulate_simpy(scenario, baseline_plan, thermal)
        for gate_id, series in des.gates.items():
            arrived = sum(series.arrivals)
            accounted = sum(series.served) + series.queue[-1]
            assert accounted == pytest.approx(arrived, rel=0.02), gate_id


class TestFifoWait:
    def test_no_wait_when_capacity_always_exceeds_arrivals(self):
        arrivals = [1.0] * 10
        served = [1.0] * 10
        assert longest_fifo_wait(arrivals, served) == 0.0

    def test_wait_grows_when_service_lags(self):
        arrivals = [10.0] + [0.0] * 9
        served = [1.0] * 10
        assert longest_fifo_wait(arrivals, served) > 0.0


class TestMonteCarlo:
    def test_percentiles_are_ordered(self, scenario, baseline_plan, thermal):
        mc = monte_carlo(scenario, baseline_plan, thermal, n=30)
        assert mc.hpm_p10 <= mc.hpm_p50 <= mc.hpm_p90
        assert mc.wait_p10 <= mc.wait_p50 <= mc.wait_p90

    def test_noise_produces_a_real_envelope(self, scenario, baseline_plan, thermal):
        mc = monte_carlo(scenario, baseline_plan, thermal, n=30)
        assert mc.hpm_p90 > mc.hpm_p10

    def test_arrival_noise_does_not_inflate_attendance(self, scenario, baseline_plan):
        """The lognormal multiplier is mean-corrected. Without the correction
        every replication would quietly add about 1 % more people."""
        totals = []
        for seed in range(60):
            import random

            profiles = arrival_profile(
                scenario, baseline_plan, random.Random(seed), noise_sigma=0.15
            )
            totals.append(sum(sum(v) for v in profiles.values()))
        expected = sum(g.total_arrivals() for g in scenario.gates)
        assert sum(totals) / len(totals) == pytest.approx(expected, rel=0.02)


class TestFeasibilityIsEnforced:
    def test_simulating_an_infeasible_plan_raises(self, scenario, thermal):
        """An over-staffed plan must never be quietly scored. If it were, the
        optimiser could win by cheating."""
        plan = Plan.baseline(scenario).with_changes(
            staff_by_block={
                g.id: {b: 50 for b in range(len(Plan.baseline(scenario).staff_by_block[g.id]))}
                for g in scenario.gates
            }
        )
        with pytest.raises(PlanInfeasibleError):
            simulate_fast(scenario, plan, thermal)
