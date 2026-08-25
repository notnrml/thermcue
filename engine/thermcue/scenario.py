"""Scenario loading and validation.

The scenario file is the operator's input: venue geometry, arrival curves,
staffing, service rates, and the hard limits every proposed change must respect.
It contains **no temperatures**. Everything thermal is derived from FortyGuard
and the forecast composition at request time, so a stale scenario file can never
put an authored temperature on a judge's screen.

Validation is strict and happens at load. A scenario that cannot be simulated
should fail on startup with a precise message, not halfway through an optimiser
run in front of an audience.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import get_settings
from .models import LngLat, ResourceType


class ScenarioError(ValueError):
    """The scenario file is not simulable. The message says exactly why."""


@dataclass(slots=True, frozen=True)
class ZoneSpec:
    id: str
    name: str
    centroid: LngLat
    polygon: list[LngLat]
    surface: str
    built_shade_fraction: float
    capacity_persons: int


@dataclass(slots=True, frozen=True)
class GateSpec:
    id: str
    name: str
    coordinates: LngLat
    queue_zone: str
    staff_count: int
    scheduled_open_hour: int
    arrivals_by_hour: dict[int, int]

    def total_arrivals(self) -> int:
        return sum(self.arrivals_by_hour.values())


@dataclass(slots=True, frozen=True)
class ServiceSpec:
    staff_per_lane: int
    service_rate_per_lane_per_min: float
    service_time_cv: float
    min_lanes_when_open: int

    def lanes_for(self, staff: int) -> int:
        """Lanes a gate can open with a given headcount.

        Integer division, floored at zero: three staff at two per lane opens one
        lane, and the spare pair of hands does not conjure a second.
        """
        return max(staff // self.staff_per_lane, 0)

    def capacity_per_hour(self, staff: int) -> float:
        return self.lanes_for(staff) * self.service_rate_per_lane_per_min * 60.0


@dataclass(slots=True, frozen=True)
class LimitsSpec:
    gate_open_offsets_min: tuple[int, ...]
    total_staff: int
    min_staff_per_gate: int
    max_staff_moves: int
    staff_block_minutes: int
    stagger_max_share: float
    stagger_offsets_min: tuple[int, ...]
    movable_resources: tuple[str, ...]
    max_wait_increase_ratio: float


@dataclass(slots=True, frozen=True)
class ResourceSpec:
    id: str
    type: ResourceType
    name: str
    coordinates: LngLat
    zone: str
    movable: bool


@dataclass(slots=True, frozen=True)
class StationSpec:
    name: str
    coordinates: LngLat
    source: str


@dataclass(slots=True, frozen=True)
class Scenario:
    id: str
    venue: str
    event_name: str
    date: str
    timezone: str
    centroid: LngLat
    start_hour: int
    end_hour: int
    expected_attendance: int
    aoi: dict[str, Any]
    zones: tuple[ZoneSpec, ...]
    gates: tuple[GateSpec, ...]
    service: ServiceSpec
    limits: LimitsSpec
    resources: tuple[ResourceSpec, ...]
    zone_edges: tuple[dict[str, Any], ...]
    station: StationSpec
    notes: tuple[str, ...]
    analogue_window_end: str | None

    @property
    def hours(self) -> tuple[int, ...]:
        return tuple(range(self.start_hour, self.end_hour + 1))

    @property
    def duration_minutes(self) -> int:
        return len(self.hours) * 60

    def zone(self, zone_id: str) -> ZoneSpec:
        for z in self.zones:
            if z.id == zone_id:
                return z
        raise KeyError(zone_id)

    def gate(self, gate_id: str) -> GateSpec:
        for g in self.gates:
            if g.id == gate_id:
                return g
        raise KeyError(gate_id)

    def baseline_staff(self) -> dict[str, int]:
        return {g.id: g.staff_count for g in self.gates}

    def baseline_open_offsets(self) -> dict[str, int]:
        return {g.id: 0 for g in self.gates}


def _lnglat(value: Any, where: str) -> LngLat:
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        raise ScenarioError(f"{where}: expected [longitude, latitude], got {value!r}")
    lon, lat = float(value[0]), float(value[1])
    # GeoJSON order catches people out constantly, and a transposed pair puts the
    # venue in the Indian Ocean where FortyGuard has no coverage at all.
    if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
        raise ScenarioError(f"{where}: {value!r} is not a valid [lon, lat] pair")
    if lon > 0 or lat < 20:
        raise ScenarioError(
            f"{where}: {value!r} does not sit in the continental United States. "
            f"FortyGuard coverage is US-only; check for transposed lat/lon."
        )
    return (lon, lat)


def parse_scenario(raw: dict[str, Any]) -> Scenario:
    """Turn raw JSON into a validated Scenario, or fail with a precise reason."""
    try:
        window = raw["time_window"]
        start_hour, end_hour = int(window["start_hour"]), int(window["end_hour"])
        service_raw = raw["service"]
        limits_raw = raw["limits"]
    except KeyError as exc:
        raise ScenarioError(f"Scenario is missing required key: {exc}") from exc

    if not 0 <= start_hour <= 23 or not 0 <= end_hour <= 23:
        raise ScenarioError("time_window hours must be within 0-23")
    if end_hour <= start_hour:
        raise ScenarioError(
            f"time_window end_hour ({end_hour}) must be after start_hour ({start_hour}). "
            f"Events crossing midnight are not supported; split them."
        )
    hours = tuple(range(start_hour, end_hour + 1))

    zones = tuple(
        ZoneSpec(
            id=z["id"],
            name=z["name"],
            centroid=_lnglat(z["centroid"], f"zone {z['id']} centroid"),
            polygon=[_lnglat(p, f"zone {z['id']} polygon") for p in z["polygon"]],
            surface=z.get("surface", "mixed"),
            built_shade_fraction=float(z.get("built_shade_fraction", 0.0)),
            capacity_persons=int(z.get("capacity_persons", 0)),
        )
        for z in raw["zones"]
    )
    zone_ids = {z.id for z in zones}
    if len(zone_ids) != len(zones):
        raise ScenarioError("zone ids must be unique")
    for z in zones:
        if z.polygon[0] != z.polygon[-1]:
            raise ScenarioError(f"zone {z.id}: polygon ring is not closed")
        if not 0.0 <= z.built_shade_fraction <= 1.0:
            raise ScenarioError(f"zone {z.id}: built_shade_fraction must be in [0, 1]")

    gates: list[GateSpec] = []
    for g in raw["gates"]:
        arrivals = {int(k): int(v) for k, v in g["arrivals_by_hour"].items()}
        missing = [h for h in hours if h not in arrivals]
        if missing:
            raise ScenarioError(f"gate {g['id']}: arrivals_by_hour is missing hours {missing}")
        if any(v < 0 for v in arrivals.values()):
            raise ScenarioError(f"gate {g['id']}: arrivals cannot be negative")
        if g["queue_zone"] not in zone_ids:
            raise ScenarioError(
                f"gate {g['id']}: queue_zone {g['queue_zone']!r} is not a declared zone. "
                f"Every gate queue must sit in a zone or its heat exposure is unweighted."
            )
        gates.append(
            GateSpec(
                id=g["id"],
                name=g["name"],
                coordinates=_lnglat(g["coordinates"], f"gate {g['id']}"),
                queue_zone=g["queue_zone"],
                staff_count=int(g["staff_count"]),
                scheduled_open_hour=int(g.get("scheduled_open_hour", start_hour)),
                arrivals_by_hour=arrivals,
            )
        )
    if len({g.id for g in gates}) != len(gates):
        raise ScenarioError("gate ids must be unique")

    service = ServiceSpec(
        staff_per_lane=int(service_raw["staff_per_lane"]),
        service_rate_per_lane_per_min=float(service_raw["service_rate_per_lane_per_min"]),
        service_time_cv=float(service_raw.get("service_time_cv", 0.0)),
        min_lanes_when_open=int(service_raw.get("min_lanes_when_open", 1)),
    )
    if service.staff_per_lane < 1 or service.service_rate_per_lane_per_min <= 0:
        raise ScenarioError("service: staff_per_lane >= 1 and a positive service rate are required")

    limits = LimitsSpec(
        gate_open_offsets_min=tuple(int(v) for v in limits_raw["gate_open_offsets_min"]),
        total_staff=int(limits_raw["total_staff"]),
        min_staff_per_gate=int(limits_raw["min_staff_per_gate"]),
        max_staff_moves=int(limits_raw["max_staff_moves"]),
        staff_block_minutes=int(limits_raw.get("staff_block_minutes", 15)),
        stagger_max_share=float(limits_raw.get("stagger_max_share", 0.0)),
        stagger_offsets_min=tuple(int(v) for v in limits_raw.get("stagger_offsets_min", [0])),
        movable_resources=tuple(limits_raw.get("movable_resources", [])),
        max_wait_increase_ratio=float(limits_raw.get("max_wait_increase_ratio", 1.10)),
    )
    baseline_total = sum(g.staff_count for g in gates)
    if baseline_total > limits.total_staff:
        raise ScenarioError(
            f"Baseline staffing ({baseline_total}) exceeds limits.total_staff "
            f"({limits.total_staff}); the scenario is infeasible as written."
        )
    if any(g.staff_count < limits.min_staff_per_gate for g in gates):
        raise ScenarioError("A gate is staffed below limits.min_staff_per_gate in the baseline")
    if 0 not in limits.gate_open_offsets_min:
        raise ScenarioError(
            "limits.gate_open_offsets_min must include 0, or the baseline plan is "
            "not inside the search space and the comparison is meaningless."
        )

    resources = tuple(
        ResourceSpec(
            id=r["id"],
            type=r["type"],
            name=r["name"],
            coordinates=_lnglat(r["coordinates"], f"resource {r['id']}"),
            zone=r["zone"],
            movable=bool(r["movable"]),
        )
        for r in raw["resources"]
    )
    for r in resources:
        if r.zone not in zone_ids:
            raise ScenarioError(f"resource {r.id}: zone {r.zone!r} is not a declared zone")
    resource_ids = {r.id for r in resources}
    for movable_id in limits.movable_resources:
        if movable_id not in resource_ids:
            raise ScenarioError(f"limits.movable_resources names unknown resource {movable_id!r}")
        if not next(r for r in resources if r.id == movable_id).movable:
            raise ScenarioError(
                f"limits.movable_resources lists {movable_id!r} but that resource is "
                f"declared movable=false. The two must agree or the optimiser will "
                f"propose moving something bolted down."
            )

    station_raw = raw["validation_station"]
    station = StationSpec(
        name=station_raw["name"],
        coordinates=_lnglat(station_raw["coordinates"], "validation_station"),
        source=station_raw.get("source", ""),
    )

    return Scenario(
        id=raw["id"],
        venue=raw["venue"],
        event_name=raw.get("event_name", raw["venue"]),
        date=raw["date"],
        timezone=raw["timezone"],
        centroid=_lnglat(raw["centroid"], "centroid"),
        start_hour=start_hour,
        end_hour=end_hour,
        expected_attendance=int(raw.get("expected_attendance", 0)),
        aoi=raw["aoi"],
        zones=zones,
        gates=tuple(gates),
        service=service,
        limits=limits,
        resources=resources,
        zone_edges=tuple(raw.get("zone_edges", [])),
        station=station,
        notes=tuple(raw.get("notes", [])),
        analogue_window_end=raw.get("analogue_window_end"),
    )


def load_scenario(path: Path | None = None) -> Scenario:
    settings = get_settings()
    target = Path(path or settings.scenario_path)
    if not target.exists():
        raise ScenarioError(f"Scenario file not found: {target}")
    return parse_scenario(json.loads(target.read_text()))


@lru_cache(maxsize=4)
def get_scenario(path: str | None = None) -> Scenario:
    """Cached scenario. Cleared by ``get_scenario.cache_clear()`` in tests."""
    return load_scenario(Path(path) if path else None)
