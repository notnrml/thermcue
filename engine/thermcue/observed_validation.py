"""Independent observed-temperature validation for FortyGuard.

This module deliberately does not import the queue simulator, shade model or
WBGT code.  Its only question is whether a FortyGuard air-temperature tile
matches an independently observed ASOS/METAR air temperature at the same place
and time, and whether it is a better local estimate than reusing KPHX across
the Phoenix metro area.

The research build script writes a fully materialised JSON report.  The engine
loads and validates that immutable report rather than making station-network or
FortyGuard calls during a judge request.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from statistics import fmean
from typing import Any

from .models import ErrorMetrics, ObservedValidationResponse, StationValidationResult


def error_metrics(errors_c: Iterable[float]) -> ErrorMetrics:
    """Return measured error statistics without inventing missing values.

    Errors are signed as ``estimate - observation``.  A positive bias therefore
    means the estimate ran hotter than the sensor on average.
    """

    values = [float(value) for value in errors_c]
    if not values:
        return ErrorMetrics(n=0)
    return ErrorMetrics(
        n=len(values),
        mae_c=fmean(abs(value) for value in values),
        bias_c=fmean(values),
        rmse_c=math.sqrt(fmean(value * value for value in values)),
        max_abs_error_c=max(abs(value) for value in values),
    )


def build_observed_validation_report(
    config: dict[str, Any],
    observations: list[dict[str, Any]],
    fortyguard_pairs: list[dict[str, Any]],
    unmatched_reasons: dict[tuple[str, str], str] | None = None,
) -> ObservedValidationResponse:
    """Build the API report from raw, reviewable paired readings.

    ``observations`` contains one nearest valid ASOS/METAR observation for each
    requested station-hour. ``fortyguard_pairs`` contains only rows for which a
    temperature tile was actually available. Missing API evidence is represented
    by the counts and ``unmatched`` list, never by an interpolated value.
    """

    station_config = {row["id"]: row for row in config["stations"]}
    airport_id = config["airport_baseline_station_id"]
    observations_by_key = {
        (row["station_id"], row["target_time_local"]): row for row in observations
    }
    airport_by_time = {
        row["target_time_local"]: row
        for row in observations
        if row["station_id"] == airport_id
    }

    enriched_pairs: list[dict[str, Any]] = []
    for raw in fortyguard_pairs:
        key = (raw["station_id"], raw["target_time_local"])
        observed = observations_by_key.get(key)
        if observed is None:
            # A FortyGuard result with no sensor observation cannot validate
            # anything. Keep it out of the paired metrics rather than matching
            # it to a different hour.
            continue
        error_c = float(raw["fortyguard_temperature_c"]) - float(
            observed["temperature_c"]
        )
        airport = airport_by_time.get(raw["target_time_local"])
        airport_temperature_c: float | None = None
        airport_error_c: float | None = None
        if raw["station_id"] != airport_id and airport is not None:
            airport_temperature_c = float(airport["temperature_c"])
            airport_error_c = airport_temperature_c - float(observed["temperature_c"])

        enriched_pairs.append(
            {
                **raw,
                "station_name": station_config[raw["station_id"]]["name"],
                "observation_time_local": observed["observation_time_local"],
                "observation_time_delta_minutes": observed[
                    "observation_time_delta_minutes"
                ],
                "observed_temperature_c": observed["temperature_c"],
                "fortyguard_error_c": round(error_c, 4),
                "fortyguard_absolute_error_c": round(abs(error_c), 4),
                "airport_temperature_c": airport_temperature_c,
                "airport_error_c": (
                    round(airport_error_c, 4) if airport_error_c is not None else None
                ),
                "airport_absolute_error_c": (
                    round(abs(airport_error_c), 4)
                    if airport_error_c is not None
                    else None
                ),
            }
        )

    enriched_pairs.sort(key=lambda row: (row["target_time_local"], row["station_id"]))

    station_errors: dict[str, list[float]] = defaultdict(list)
    station_airport_errors: dict[str, list[float]] = defaultdict(list)
    for row in enriched_pairs:
        station_errors[row["station_id"]].append(row["fortyguard_error_c"])
        if row["airport_error_c"] is not None:
            station_airport_errors[row["station_id"]].append(row["airport_error_c"])

    station_results = [
        StationValidationResult(
            station_id=station_id,
            station_name=station_config[station_id]["name"],
            latitude=station_config[station_id]["latitude"],
            longitude=station_config[station_id]["longitude"],
            fortyguard=error_metrics(station_errors.get(station_id, [])),
            airport_baseline=error_metrics(station_airport_errors.get(station_id, [])),
        )
        for station_id in station_config
    ]

    fortyguard_errors = [row["fortyguard_error_c"] for row in enriched_pairs]
    comparable_fortyguard_errors = [
        row["fortyguard_error_c"]
        for row in enriched_pairs
        if row["airport_error_c"] is not None
    ]
    airport_errors = [
        row["airport_error_c"]
        for row in enriched_pairs
        if row["airport_error_c"] is not None
    ]
    comparable = [
        row for row in enriched_pairs if row["airport_absolute_error_c"] is not None
    ]
    fortyguard_better = sum(
        row["fortyguard_absolute_error_c"] < row["airport_absolute_error_c"]
        for row in comparable
    )
    airport_better = sum(
        row["fortyguard_absolute_error_c"] > row["airport_absolute_error_c"]
        for row in comparable
    )
    ties = len(comparable) - fortyguard_better - airport_better

    expected_keys = {
        (station["id"], f"{date}T{hour:02d}:00:00")
        for station in config["stations"]
        for date in config["dates"]
        for hour in config["hours"]
    }
    observed_keys = set(observations_by_key)
    paired_keys = {
        (row["station_id"], row["target_time_local"]) for row in enriched_pairs
    }
    unmatched_reasons = unmatched_reasons or {}
    unmatched = [
        {
            "stationId": station_id,
            "targetTimeLocal": target_time,
            "reason": (
                "no_station_observation"
                if (station_id, target_time) not in observed_keys
                else unmatched_reasons.get(
                    (station_id, target_time), "not_collected"
                )
            ),
        }
        for station_id, target_time in sorted(expected_keys - paired_keys)
    ]

    if len(paired_keys) == len(expected_keys):
        status = "complete"
    elif paired_keys:
        status = "partial"
    else:
        status = "unavailable"

    return ObservedValidationResponse(
        status=status,
        study_name=config["study_name"],
        dates=config["dates"],
        hours=config["hours"],
        timezone=config["timezone"],
        matching_tolerance_minutes=config["matching_tolerance_minutes"],
        station_source=config["observation_source"],
        fortyguard_source="FortyGuard /v1/heatmap, analytic_type=tcm",
        airport_baseline_station_id=airport_id,
        expected_station_hours=len(expected_keys),
        observed_station_hours=len(observed_keys & expected_keys),
        paired_station_hours=len(paired_keys),
        fortyguard=error_metrics(fortyguard_errors),
        fortyguard_comparable=error_metrics(comparable_fortyguard_errors),
        airport_baseline=error_metrics(airport_errors),
        comparable_station_hours=len(comparable),
        fortyguard_better_count=fortyguard_better,
        airport_better_count=airport_better,
        tie_count=ties,
        station_results=station_results,
        pairs=enriched_pairs,
        unmatched=unmatched,
        limitations=[
            "This validates air temperature only; it does not validate estimated WBGT, shade, queues or health outcomes.",
            "ASOS/METAR sensors are airport observations. A venue deployment still needs its own on-site sensor check.",
            "No bias correction is applied. A correction would require fitting on earlier dates and improving error on held-out dates.",
        ],
    )


def load_observed_validation(path: Path) -> ObservedValidationResponse:
    """Load the committed report and validate its wire contract."""

    return ObservedValidationResponse.model_validate_json(Path(path).read_text())


def write_observed_validation(
    path: Path, report: ObservedValidationResponse
) -> None:
    """Write deterministic, reviewable JSON for the engine and the repository."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(by_alias=True), indent=2, sort_keys=True) + "\n"
    )
