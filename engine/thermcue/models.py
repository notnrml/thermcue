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
    driver_score: float | None = Field(default=None, ge=0.0, le=1.0)
    driver_narrative: str | None = None

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
    queue_length: float
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


# ---------------------------------------------- observed validation ---


class ErrorMetrics(Wire):
    """Measured estimate-minus-observation errors in degrees Celsius."""

    n: int = Field(ge=0)
    mae_c: float | None = Field(default=None, ge=0.0)
    bias_c: float | None = None
    rmse_c: float | None = Field(default=None, ge=0.0)
    max_abs_error_c: float | None = Field(default=None, ge=0.0)


class StationValidationResult(Wire):
    station_id: str
    station_name: str
    latitude: float
    longitude: float
    fortyguard: ErrorMetrics
    airport_baseline: ErrorMetrics


class ObservedValidationPair(Wire):
    station_id: str
    station_name: str
    latitude: float
    longitude: float
    target_time_local: str
    observation_time_local: str
    observation_time_delta_minutes: float = Field(ge=0.0)
    observed_temperature_c: float
    fortyguard_temperature_c: float
    fortyguard_error_c: float
    fortyguard_absolute_error_c: float = Field(ge=0.0)
    airport_temperature_c: float | None = None
    airport_error_c: float | None = None
    airport_absolute_error_c: float | None = Field(default=None, ge=0.0)
    fortyguard_activity_id: str | None = None
    fortyguard_freshness: DataFreshness
    fortyguard_tile_id: str
    fortyguard_tile_longitude: float
    fortyguard_tile_latitude: float
    station_to_tile_distance_m: float = Field(ge=0.0)
    fortyguard_cache_file: str


class ObservedValidationResponse(Wire):
    """Independent station-to-FortyGuard temperature validation."""

    status: Literal["complete", "partial", "unavailable"]
    study_name: str
    dates: list[str]
    hours: list[int]
    timezone: str
    matching_tolerance_minutes: int = Field(gt=0)
    station_source: str
    fortyguard_source: str
    airport_baseline_station_id: str
    expected_station_hours: int = Field(ge=0)
    observed_station_hours: int = Field(ge=0)
    paired_station_hours: int = Field(ge=0)
    # All accepted FortyGuard-to-sensor pairs, including KPHX itself.
    fortyguard: ErrorMetrics
    # Only non-KPHX rows for which both methods estimate the same observation.
    # This is the apples-to-apples counterpart to ``airport_baseline``.
    fortyguard_comparable: ErrorMetrics
    airport_baseline: ErrorMetrics
    comparable_station_hours: int = Field(ge=0)
    fortyguard_better_count: int = Field(ge=0)
    airport_better_count: int = Field(ge=0)
    tie_count: int = Field(ge=0)
    station_results: list[StationValidationResult]
    pairs: list[ObservedValidationPair]
    unmatched: list[dict]
    limitations: list[str]


class ObservedValidationSummary(Wire):
    """Small judge-facing slice of the independent validation report.

    The full report remains available from ``/validation/observed``. The plan
    payload carries only the fields needed to label the UI without shipping all
    raw station pairs on every workspace request.
    """

    status: Literal["complete", "partial", "unavailable"]
    study_name: str
    expected_station_hours: int = Field(ge=0)
    observed_station_hours: int = Field(ge=0)
    paired_station_hours: int = Field(ge=0)
    comparable_station_hours: int = Field(ge=0)
    fortyguard_comparable: ErrorMetrics
    airport_baseline: ErrorMetrics
    fortyguard_better_count: int = Field(ge=0)
    airport_better_count: int = Field(ge=0)
    tie_count: int = Field(ge=0)
    limitations: list[str]


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
    observed_validation: ObservedValidationSummary | None = None


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
