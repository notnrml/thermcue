"""Wire contracts.

These Pydantic models are the server half of the contract declared in
``web/types/index.ts``. Field names use the camelCase the TypeScript side
expects, via ``alias`` plus ``populate_by_name``, so Python code stays snake_case
and the JSON stays exactly what the components already consume. If you change a
field here, change it there in the same commit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

WbgtBand = Literal["low", "moderate", "high", "extreme"]
DataFreshness = Literal["live", "cached"]
ResourceType = Literal["water", "rest"]
PlanChangeKind = Literal["gate", "staff", "water", "rest"]
ParetoPointKind = Literal["baseline", "candidate", "chosen"]
AgentFeedType = Literal["monitor", "replan", "directive", "no-action"]
ReadingKind = Literal["historical", "current", "forecast"]

#: GeoJSON order: [longitude, latitude]. Mirrors the ``LngLat`` tuple in the UI.
LngLat = tuple[float, float]


class Wire(BaseModel):
    """Base for anything crossing the HTTP boundary to the Next.js app."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        ser_json_inf_nan="null",
    )


# --------------------------------------------------------------- scenario ---


class Zone(Wire):
    id: str
    name: str
    polygon: list[LngLat]
    wbgt_band: WbgtBand
    temperature_c: float
    shade_coverage: float = Field(ge=0.0, le=1.0)

    @field_validator("polygon")
    @classmethod
    def _closed_ring(cls, v: list[LngLat]) -> list[LngLat]:
        if len(v) < 4:
            raise ValueError("polygon needs at least 4 positions (a closed triangle)")
        if v[0] != v[-1]:
            raise ValueError("polygon ring must be closed: first position must equal last")
        return v


class HourlyZoneState(Wire):
    zone_id: str
    hour: int = Field(ge=0, le=23)
    wbgt_band: WbgtBand
    temperature_c: float
    shade_coverage: float = Field(ge=0.0, le=1.0)


class Gate(Wire):
    id: str
    name: str
    coordinates: LngLat
    capacity: int = Field(gt=0, description="Throughput in persons per hour at current lanes.")
    lanes: int = Field(gt=0)
    staff_count: int = Field(ge=0)
    queue_length: int = Field(ge=0)
    wait_time_minutes: float = Field(ge=0)


class Resource(Wire):
    id: str
    type: ResourceType
    name: str
    coordinates: LngLat
    movable: bool


class TimeWindow(Wire):
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=23)


class ScenarioEvent(Wire):
    id: str
    venue: str
    date: str
    time_window: TimeWindow
    timezone: str
    data_freshness: DataFreshness
    zones: list[Zone]
    gates: list[Gate]
    resources: list[Resource]


# ------------------------------------------------------------- simulation ---


class QueueState(Wire):
    gate_id: str
    hour: int
    arrivals: int
    wait_time_minutes: float
    person_minutes: float


class KpiSet(Wire):
    heat_weighted_person_minutes: float
    person_minutes_high_extreme: float
    total_wait_minutes: float
    longest_wait_minutes: float


class KpiComparison(Wire):
    baseline: KpiSet
    optimised: KpiSet


class WbgtHourly(Wire):
    hour: int
    p10: float
    p50: float
    p90: float
    venue_max: float


# -------------------------------------------------------------- optimiser ---


class WhyTraceStep(Wire):
    stage: str
    detail: str


class PlanChange(Wire):
    id: str
    kind: PlanChangeKind
    action: str
    time_chips: list[str]
    why_trace: list[WhyTraceStep]
    counterfactual_percent: float


class ParetoPoint(Wire):
    id: str
    total_wait_minutes: float
    heat_weighted_exposure: float
    kind: ParetoPointKind


# ------------------------------------------------------------------ agent ---


class ToolTrace(Wire):
    tool: str
    input: str
    output: str


class AgentFeedEntry(Wire):
    id: str
    timestamp: str
    type: AgentFeedType
    text: str
    tool_trace: list[ToolTrace]


# ------------------------------------------------------------- validation ---


class ValidationPoint(Wire):
    hour: int
    zone_id: str
    zone_temp_c: float
    station_temp_c: float


class ValidationSummary(Wire):
    max_intra_venue_spread_c: float
    verdict_decision: str


class ValidationResponse(Wire):
    points: list[ValidationPoint]
    summary: ValidationSummary
    station_name: str
    station_source: str


# ---------------------------------------------------------------- bundles ---


class PlanWorkspaceData(Wire):
    """Everything the Plan Workspace page needs, in one payload."""

    scenario: ScenarioEvent
    hourly_zone_states: list[HourlyZoneState]
    queue_states: list[QueueState]
    kpis: KpiComparison
    pareto_points: list[ParetoPoint]
    plan_changes: list[PlanChange]
    agent_feed: list[AgentFeedEntry]
    validation_points: list[ValidationPoint]
    validation_summary: ValidationSummary
    wbgt_hourly: list[WbgtHourly]


# ------------------------------------------------- internal thermal types ---


class TempReading(Wire):
    """One temperature observation at a point in space and time.

    ``kind`` records provenance honestly. FortyGuard's catalogue runs 2021 to
    today and rejects future dates, so a reading of kind ``forecast`` is never
    sourced from FortyGuard; see ``thermcue.forecast`` for how the forward view
    is constructed and which source supplies which half of it.
    """

    lat: float
    lon: float
    t_air_c: float
    rh_pct: float | None = None
    ts: datetime
    kind: ReadingKind
    source: str
    """Provenance string, e.g. ``fortyguard:heatmap`` or ``open-meteo:forecast``."""


class ZoneHourThermal(Wire):
    """The full derived thermal state of one zone in one hour."""

    zone_id: str
    hour: int
    t_air_c: float
    rh_pct: float
    wind_ms: float
    solar_ghi_wm2: float
    t_wet_bulb_c: float
    t_globe_c: float
    wbgt_iso_c: float
    """ISO 7243 outdoor WBGT from wet bulb, globe and air temperature."""
    wbgt_abm_c: float
    """The ABM/Australian-Bureau simplification, carried as a cross-check only."""
    shaded_fraction: float
    wbgt_shade_adjusted_c: float
    band: WbgtBand
    driver_score: float | None = None
    driver_narrative: str | None = None


class ThermalResponse(Wire):
    zones: list[ZoneHourThermal]
    freshness: DataFreshness
    sources: dict[str, str]
    notes: list[str]
