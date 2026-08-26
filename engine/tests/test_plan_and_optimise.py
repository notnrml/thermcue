"""Plan feasibility and optimiser tests.

The optimiser's credibility rests on two claims: that it never proposes
something the operator did not authorise, and that the improvement it reports
is real rather than an artefact of the metric. Both are asserted here.
"""

from __future__ import annotations

import pytest

from thermcue.plan import Plan, PlanInfeasibleError, block_count
from thermcue.optimise import (
    explain,
    optimise,
    optimise_resources,
    pareto_from_archive,
    pareto_scatter,
    propose_staffing,
    run_full_optimisation,
    staff_to_clear,
)
from thermcue.simulate import HEADLINE_SEED, simulate_fast, weight_sensitivity


class TestPlanFeasibility:
    def test_baseline_is_feasible(self, scenario, baseline_plan):
        baseline_plan.validate_against(scenario)

    def test_over_total_staff_is_rejected(self, scenario, baseline_plan):
        plan = baseline_plan.with_changes(
            staff_by_block={
                g.id: {b: 20 for b in range(block_count(scenario))} for g in scenario.gates
            }
        )
        with pytest.raises(PlanInfeasibleError, match="only 21"):
            plan.validate_against(scenario)

    def test_below_minimum_staff_is_rejected(self, scenario, baseline_plan):
        allocation = {g.id: dict(baseline_plan.staff_by_block[g.id]) for g in scenario.gates}
        allocation["g-d"][0] = 0
        with pytest.raises(PlanInfeasibleError, match="below the minimum"):
            baseline_plan.with_changes(staff_by_block=allocation).validate_against(scenario)

    def test_unpermitted_gate_offset_is_rejected(self, scenario, baseline_plan):
        plan = baseline_plan.with_changes(gate_open_offset_min={"g-a": -20})
        with pytest.raises(PlanInfeasibleError, match="outside the permitted"):
            plan.validate_against(scenario)

    def test_too_many_staff_moves_is_rejected(self, scenario, baseline_plan):
        """Every move is a radio call and a walk across a hot site. The cap is
        an operational limit, not a solver hint."""
        allocation = {g.id: dict(baseline_plan.staff_by_block[g.id]) for g in scenario.gates}
        for block in range(0, 12, 2):
            allocation["g-a"][block] = 8 if block % 4 == 0 else 6
            allocation["g-b"][block] = 6 if block % 4 == 0 else 8
        with pytest.raises(PlanInfeasibleError, match="staff moves"):
            baseline_plan.with_changes(staff_by_block=allocation).validate_against(scenario)

    def test_stagger_above_the_cap_is_rejected(self, scenario, baseline_plan):
        plan = baseline_plan.with_changes(stagger_share=0.9, stagger_offset_min=15)
        with pytest.raises(PlanInfeasibleError, match="Stagger share"):
            plan.validate_against(scenario)

    def test_moving_a_fixed_resource_is_rejected(self, scenario, baseline_plan):
        """r-w3 is bolted to the concourse. The optimiser must not be able to
        propose relocating it."""
        plan = baseline_plan.with_changes(resource_zone={"r-w3": "z-lawn"})
        with pytest.raises(PlanInfeasibleError, match="fixed"):
            plan.validate_against(scenario)

    def test_unknown_gate_is_rejected(self, scenario, baseline_plan):
        plan = baseline_plan.with_changes(gate_open_offset_min={"g-zzz": 0})
        with pytest.raises(PlanInfeasibleError, match="unknown gates"):
            plan.validate_against(scenario)


class TestPlanDiff:
    def test_identical_plans_have_no_diff(self, scenario, baseline_plan):
        assert baseline_plan.diff(baseline_plan, scenario) == []

    def test_gate_offset_change_appears(self, scenario, baseline_plan):
        other = baseline_plan.with_changes(
            gate_open_offset_min={**baseline_plan.gate_open_offset_min, "g-c": -45}
        )
        changes = baseline_plan.diff(other, scenario)
        assert len(changes) == 1
        assert changes[0]["kind"] == "gate" and changes[0]["after"] == -45

    def test_staff_change_appears_per_block(self, scenario, baseline_plan):
        allocation = {g.id: dict(baseline_plan.staff_by_block[g.id]) for g in scenario.gates}
        allocation["g-d"][4] = 5
        other = baseline_plan.with_changes(staff_by_block=allocation)
        changes = baseline_plan.diff(other, scenario)
        assert [c["kind"] for c in changes] == ["staff"]
        assert changes[0]["block"] == 4


class TestStaffingProposals:
    def test_staff_to_clear_grows_with_demand(self, scenario):
        assert staff_to_clear(scenario, 1000.0) > staff_to_clear(scenario, 100.0)

    def test_proposals_are_all_feasible(self, scenario, thermal):
        """CP-SAT models the limits itself, but the plan validator is the
        authority. Any disagreement between them is a bug in the model."""
        for allocation in propose_staffing(scenario, thermal):
            Plan.baseline(scenario).with_changes(staff_by_block=allocation).validate_against(
                scenario
            )

    def test_no_gate_is_starved_below_its_structural_need(self, scenario, thermal):
        """The regression that mattered: an earlier objective proposed 15 staff
        on one gate and the floor everywhere else, leaving an abandoned gate
        queueing all night."""
        for allocation in propose_staffing(scenario, thermal):
            for gate in scenario.gates:
                total_capacity = sum(
                    scenario.service.capacity_per_hour(staff)
                    * (scenario.limits.staff_block_minutes / 60.0)
                    for staff in allocation[gate.id].values()
                )
                assert total_capacity >= gate.total_arrivals() * 0.9, gate.id


class TestOptimiser:
    def test_optimised_plan_is_feasible(self, scenario, thermal):
        _, best, _ = optimise(scenario, thermal)
        best.plan.validate_against(scenario)

    def test_optimiser_never_worsens_exposure(self, scenario, thermal):
        """The baseline is always in the search space, so the result can only be
        at least as good. If this fails, the search is dropping the incumbent."""
        baseline, best, _ = optimise(scenario, thermal)
        assert best.hpm <= baseline.hpm + 1e-6

    def test_wait_constraint_is_respected(self, scenario, thermal):
        baseline, best, _ = optimise(scenario, thermal, wait_ratio=1.10)
        assert best.total_wait <= baseline.total_wait * 1.10 + 1e-6

    def test_a_tighter_wait_constraint_cannot_do_better(self, scenario, thermal):
        """Monotonicity of the frontier: a smaller feasible set cannot contain a
        better optimum."""
        _, tight, _ = optimise(scenario, thermal, wait_ratio=1.00)
        _, loose, _ = optimise(scenario, thermal, wait_ratio=1.20)
        assert loose.hpm <= tight.hpm + 1e-6

    def test_acceptance_gate_from_the_brief(self, scenario, optimisation):
        """The brief's stated acceptance criterion: at least a 20 % HPM
        reduction at no more than a 10 % wait increase on the demo scenario."""
        result = optimisation
        assert result.hpm_reduction_pct >= 20.0
        assert result.wait_change_pct <= 10.0

    def test_every_change_carries_a_populated_why_object(self, scenario, optimisation):
        """Also from the brief: no change ships without an explanation."""
        result = optimisation
        assert result.changes
        for change in result.changes:
            assert change.action
            assert change.binding_condition
            assert change.band_and_hour
            assert change.hours
            assert change.counterfactual_share_pct >= 0.0

    def test_explanations_cite_the_baseline_queue_not_the_optimised_one(
        self, scenario, optimisation
    ):
        """Citing the optimised queue produces "open Gate C early because the
        predicted queue is 0" - which it is, because the gate was opened early."""
        result = optimisation
        gate_changes = [c for c in result.changes if c.kind in ("gate", "staff")]
        assert gate_changes
        assert any(c.predicted_queue > 0 for c in gate_changes)

    def test_staff_reallocation_is_one_feasible_counterfactual(
        self, scenario, optimisation
    ):
        """A fixed-headcount transfer must never be attributed by reverting
        only its donor or receiver, which would create the wrong staff total."""

        staff_changes = [c for c in optimisation.changes if c.kind == "staff"]
        assert len(staff_changes) <= 1
        if staff_changes:
            change = staff_changes[0]
            assert change.action.startswith("Reallocate staff:")
            assert change.raw["kind"] == "staff_reallocation"
            assert len(change.raw["staff_runs"]) >= 2
            for block in range(block_count(scenario)):
                total = sum(
                    optimisation.optimised.plan.staff_at(gate.id, block)
                    for gate in scenario.gates
                )
                assert total == scenario.limits.total_staff

    def test_counterfactual_shares_are_normalised(self, scenario, optimisation):
        result = optimisation
        total = sum(c.counterfactual_share_pct for c in result.changes)
        assert total == pytest.approx(100.0, abs=0.5)


class TestPareto:
    def test_frontier_is_monotonic_in_the_wait_allowance(self, scenario, optimisation):
        result = optimisation
        exposures = [p["heat_weighted_exposure"] for p in result.pareto]
        assert exposures == sorted(exposures, reverse=True)

    def test_scatter_contains_baseline_and_chosen(self, scenario, optimisation):
        result = optimisation
        kinds = [p["kind"] for p in result.pareto_scatter]
        assert kinds.count("baseline") == 1
        assert kinds.count("chosen") == 1
        assert "candidate" in kinds

    def test_frontier_points_are_real_scored_candidates(self, scenario, optimisation):
        """Nothing on the chart is interpolated: every point was simulated."""
        result = optimisation
        for point in result.pareto:
            assert point["total_wait_minutes"] > 0
            assert point["heat_weighted_exposure"] >= 0


class TestResources:
    def test_only_movable_resources_are_relocated(self, scenario, optimisation):
        result = optimisation
        movable = set(scenario.limits.movable_resources)
        for move in result.resource_moves:
            assert move["resource_id"] in movable

    def test_relocations_go_somewhere_different(self, scenario, optimisation):
        result = optimisation
        for move in result.resource_moves:
            assert move["from_zone"] != move["to_zone"]

    def test_water_and_rest_are_covered_independently(self, scenario, thermal):
        """A rest tent does not relieve thirst. Treating the two as
        interchangeable pinned every movable resource in place."""
        baseline = simulate_fast(scenario, Plan.baseline(scenario), thermal)
        moves = optimise_resources(scenario, thermal, baseline)
        targets = [(m["type"], m["to_zone"]) for m in moves]
        assert len(targets) == len(set(targets))


class TestMetricDefence:
    def test_the_winning_plan_wins_under_every_weighting(self, scenario, thermal):
        """If the improvement flips sign under a plausible alternative
        weighting, it is an artefact of the weights and not of the plan. The
        README reports whatever this shows."""
        _, best, _ = optimise(scenario, thermal)
        table = weight_sensitivity(scenario, Plan.baseline(scenario), best.plan, thermal)
        assert all(row["optimised_wins"] for row in table.values()), table

    def test_sensitivity_covers_the_headline_weighting(self, scenario, thermal):
        _, best, _ = optimise(scenario, thermal)
        table = weight_sensitivity(scenario, Plan.baseline(scenario), best.plan, thermal)
        assert "headline-0124" in table
        assert len(table) >= 4
