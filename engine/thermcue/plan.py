"""The plan object: every lever the optimiser may pull, and nothing else.

A plan is the only thing that varies between simulation runs. Keeping it a
single frozen structure means "what changed" is a diff between two objects
rather than a comparison of scattered arguments, which is what the agent's
``diff_plans`` tool and the UI's change list both need.

Every field here maps to something a venue operations manager can authorise on
the day. ``validate_against`` refuses anything outside the scenario's declared
limits, and it is called on every plan before it is scored, so an infeasible
plan cannot win by being unchecked.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .scenario import Scenario


class PlanInfeasibleError(ValueError):
    """A plan violates the scenario's operating limits. Never scored, never shown."""


@dataclass(slots=True, frozen=True)
class Plan:
    """One candidate operating plan.

    ``staff_by_block`` is gate id -> block index -> headcount, where a block is
    ``limits.staff_block_minutes`` long and block 0 starts at the event start
    hour. Integer headcount only: half a marshal does not exist, and a continuous
    relaxation would let the optimiser buy throughput it cannot staff.
    """

    gate_open_offset_min: dict[str, int] = field(default_factory=dict)
    staff_by_block: dict[str, dict[int, int]] = field(default_factory=dict)
    stagger_share: float = 0.0
    stagger_offset_min: int = 0
    resource_zone: dict[str, str] = field(default_factory=dict)
    label: str = "baseline"

    # --- construction ----------------------------------------------------

    @classmethod
    def baseline(cls, scenario: Scenario) -> "Plan":
        """The plan as scheduled: no offsets, no reallocation, no stagger.

        Every comparison in the product is against this, so it must be a real
        member of the search space rather than a special case.
        """
        blocks = block_count(scenario)
        return cls(
            gate_open_offset_min={g.id: 0 for g in scenario.gates},
            staff_by_block={
                g.id: {b: g.staff_count for b in range(blocks)} for g in scenario.gates
            },
            stagger_share=0.0,
            stagger_offset_min=0,
            resource_zone={r.id: r.zone for r in scenario.resources},
            label="baseline",
        )

    def with_changes(self, **kwargs: Any) -> "Plan":
        return replace(self, **kwargs)

    def staff_at(self, gate_id: str, block: int) -> int:
        return self.staff_by_block.get(gate_id, {}).get(block, 0)

    # --- feasibility -----------------------------------------------------

    def validate_against(self, scenario: Scenario) -> None:
        """Raise ``PlanInfeasibleError`` describing the first violated limit."""
        limits = scenario.limits
        blocks = block_count(scenario)
        gate_ids = {g.id for g in scenario.gates}

        unknown = set(self.gate_open_offset_min) - gate_ids
        if unknown:
            raise PlanInfeasibleError(f"Plan references unknown gates: {sorted(unknown)}")

        for gate_id, offset in self.gate_open_offset_min.items():
            if offset not in limits.gate_open_offsets_min:
                raise PlanInfeasibleError(
                    f"Gate {gate_id}: open offset {offset} min is outside the permitted "
                    f"set {list(limits.gate_open_offsets_min)}"
                )

        for block in range(blocks):
            total = sum(self.staff_at(g.id, block) for g in scenario.gates)
            if total > limits.total_staff:
                raise PlanInfeasibleError(
                    f"Block {block}: {total} staff assigned but only "
                    f"{limits.total_staff} exist"
                )
            for gate in scenario.gates:
                staffed = self.staff_at(gate.id, block)
                if staffed < limits.min_staff_per_gate:
                    raise PlanInfeasibleError(
                        f"Gate {gate.id} block {block}: {staffed} staff is below the "
                        f"minimum of {limits.min_staff_per_gate}"
                    )

        moves = self.staff_move_count(scenario)
        if moves > limits.max_staff_moves:
            raise PlanInfeasibleError(
                f"Plan makes {moves} staff moves; the limit is {limits.max_staff_moves}. "
                f"Each move is a real radio call and a walk across a hot site."
            )

        if not 0.0 <= self.stagger_share <= limits.stagger_max_share:
            raise PlanInfeasibleError(
                f"Stagger share {self.stagger_share:.2f} is outside "
                f"[0, {limits.stagger_max_share:.2f}]"
            )
        if self.stagger_offset_min not in limits.stagger_offsets_min:
            raise PlanInfeasibleError(
                f"Stagger offset {self.stagger_offset_min} min is outside the permitted "
                f"set {list(limits.stagger_offsets_min)}"
            )

        zone_ids = {z.id for z in scenario.zones}
        for resource_id, zone_id in self.resource_zone.items():
            if zone_id not in zone_ids:
                raise PlanInfeasibleError(
                    f"Resource {resource_id} assigned to unknown zone {zone_id!r}"
                )
            original = next((r for r in scenario.resources if r.id == resource_id), None)
            if original is None:
                raise PlanInfeasibleError(f"Plan references unknown resource {resource_id!r}")
            if zone_id != original.zone and resource_id not in limits.movable_resources:
                raise PlanInfeasibleError(
                    f"Resource {resource_id} is fixed but the plan moves it from "
                    f"{original.zone} to {zone_id}"
                )

    def staff_move_count(self, scenario: Scenario) -> int:
        """Number of distinct staff reallocations relative to the baseline.

        A gate whose headcount changes at a block boundary is one move,
        regardless of how many people walk, because the operational cost is the
        radio call and the reshuffle rather than the headcount.
        """
        moves = 0
        for gate in scenario.gates:
            previous = gate.staff_count
            for block in range(block_count(scenario)):
                current = self.staff_at(gate.id, block)
                if current != previous:
                    moves += 1
                previous = current
        return moves

    # --- diffing ---------------------------------------------------------

    def diff(self, other: "Plan", scenario: Scenario) -> list[dict[str, Any]]:
        """Structured difference from ``self`` to ``other``.

        Powers the agent's ``diff_plans`` tool and the UI change list, so the
        shape here is the shape a directive cites.
        """
        changes: list[dict[str, Any]] = []
        for gate in scenario.gates:
            before = self.gate_open_offset_min.get(gate.id, 0)
            after = other.gate_open_offset_min.get(gate.id, 0)
            if before != after:
                changes.append(
                    {
                        "kind": "gate",
                        "gate_id": gate.id,
                        "gate_name": gate.name,
                        "field": "open_offset_min",
                        "before": before,
                        "after": after,
                    }
                )
        for gate in scenario.gates:
            for block in range(block_count(scenario)):
                before = self.staff_at(gate.id, block)
                after = other.staff_at(gate.id, block)
                if before != after:
                    changes.append(
                        {
                            "kind": "staff",
                            "gate_id": gate.id,
                            "gate_name": gate.name,
                            "block": block,
                            "before": before,
                            "after": after,
                        }
                    )
        if (self.stagger_share, self.stagger_offset_min) != (
            other.stagger_share,
            other.stagger_offset_min,
        ):
            changes.append(
                {
                    "kind": "stagger",
                    "before": {"share": self.stagger_share, "offset_min": self.stagger_offset_min},
                    "after": {"share": other.stagger_share, "offset_min": other.stagger_offset_min},
                }
            )
        for resource in scenario.resources:
            before = self.resource_zone.get(resource.id, resource.zone)
            after = other.resource_zone.get(resource.id, resource.zone)
            if before != after:
                changes.append(
                    {
                        "kind": resource.type,
                        "resource_id": resource.id,
                        "resource_name": resource.name,
                        "before": before,
                        "after": after,
                    }
                )
        return changes


def block_count(scenario: Scenario) -> int:
    """Number of staffing blocks across the event window."""
    return scenario.duration_minutes // scenario.limits.staff_block_minutes


def block_for_minute(scenario: Scenario, minute: int) -> int:
    """Block index containing an event minute, clamped to the last block."""
    return min(minute // scenario.limits.staff_block_minutes, block_count(scenario) - 1)
