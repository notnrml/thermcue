"""Build independent ASOS/METAR-to-FortyGuard validation evidence.

Milestone 1 downloads actual airport sensor observations. Milestone 2 pairs
those observations with FortyGuard tcm tiles at the same station and hour,
first reusing any committed response and optionally making missing live calls.
Milestone 3 compares the local FortyGuard error with the error from reusing the
KPHX observation across the metro area. The final JSON is served by the engine's
``GET /validation/observed`` endpoint.

Examples from the repository root::

    # Rebuild observations and use only already committed FortyGuard responses.
    engine/.venv/bin/python research/scripts/build_observed_validation.py

    # Populate every missing station-hour with the configured FortyGuard key.
    FORTYGUARD_API_KEY=... engine/.venv/bin/python \
      research/scripts/build_observed_validation.py --fetch-fortyguard

Keys are read only from the environment and never written to an artefact.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from thermcue.fortyguard.cache import canonical_key
from thermcue.observed_validation import (
    build_observed_validation_report,
    write_observed_validation,
)

DEFAULT_CONFIG = ROOT / "research" / "observed_validation_config.json"
OBSERVATIONS_PATH = ROOT / "research" / "data" / "phoenix_asos_observations.csv"
PAIRS_PATH = ROOT / "research" / "data" / "fortyguard_station_pairs.json"
OUTCOMES_PATH = ROOT / "research" / "data" / "fortyguard_collection_outcomes.json"
REPORT_PATH = ROOT / "research" / "observed_validation.json"
CACHE_DIR = ROOT / "engine" / "data" / "cache"

OBSERVATION_COLUMNS = (
    "station_id",
    "station_name",
    "latitude",
    "longitude",
    "target_time_local",
    "observation_time_local",
    "observation_time_delta_minutes",
    "temperature_c",
    "relative_humidity_pct",
    "wind_speed_ms",
    "source",
)


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)


def _iem_url(config: dict[str, Any], date: str) -> str:
    start = datetime.fromisoformat(date)
    end = start + timedelta(days=1)
    params: list[tuple[str, str]] = []
    params.extend(("station", station["id"]) for station in config["stations"])
    params.extend(("data", field) for field in ("tmpf", "relh", "sknt"))
    params.extend(
        [
            ("year1", str(start.year)),
            ("month1", str(start.month)),
            ("day1", str(start.day)),
            ("year2", str(end.year)),
            ("month2", str(end.month)),
            ("day2", str(end.day)),
            ("tz", config["timezone"]),
            ("format", "onlycomma"),
            ("latlon", "yes"),
            ("elev", "no"),
            ("missing", "empty"),
            ("trace", "empty"),
            ("direct", "no"),
            ("report_type", "1"),
            ("report_type", "2"),
        ]
    )
    return f"{config['observation_source_url']}?{urlencode(params)}"


def download_observations(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Download and nearest-match actual station observations."""

    raw_by_station: dict[str, list[dict[str, Any]]] = {
        station["id"]: [] for station in config["stations"]
    }
    for date in config["dates"]:
        request = Request(
            _iem_url(config, date),
            headers={"User-Agent": "ThermCue observed validation/1.0"},
        )
        with urlopen(request, timeout=60) as response:
            text = response.read().decode("utf-8")
        for row in csv.DictReader(text.splitlines()):
            temperature_f = _optional_float(row.get("tmpf"))
            if temperature_f is None or row.get("station") not in raw_by_station:
                continue
            observed_at = datetime.strptime(
                row["valid"], "%Y-%m-%d %H:%M"
            ).replace(tzinfo=ZoneInfo(config["timezone"]))
            if observed_at.date().isoformat() != date:
                continue
            raw_by_station[row["station"]].append(
                {
                    "observed_at": observed_at,
                    "temperature_c": (temperature_f - 32.0) * 5.0 / 9.0,
                    "relative_humidity_pct": _optional_float(row.get("relh")),
                    "wind_speed_ms": (
                        _optional_float(row.get("sknt")) * 0.514444
                        if _optional_float(row.get("sknt")) is not None
                        else None
                    ),
                }
            )

    tolerance = float(config["matching_tolerance_minutes"])
    output: list[dict[str, Any]] = []
    for station in config["stations"]:
        for date in config["dates"]:
            for hour in config["hours"]:
                target = datetime.fromisoformat(
                    f"{date}T{hour:02d}:00:00"
                ).replace(tzinfo=ZoneInfo(config["timezone"]))
                candidates = raw_by_station[station["id"]]
                if not candidates:
                    continue
                nearest = min(
                    candidates,
                    key=lambda row: abs((row["observed_at"] - target).total_seconds()),
                )
                delta_minutes = abs(
                    (nearest["observed_at"] - target).total_seconds()
                ) / 60.0
                if delta_minutes > tolerance:
                    continue
                output.append(
                    {
                        "station_id": station["id"],
                        "station_name": station["name"],
                        "latitude": station["latitude"],
                        "longitude": station["longitude"],
                        "target_time_local": target.replace(tzinfo=None).isoformat(),
                        "observation_time_local": nearest["observed_at"]
                        .replace(tzinfo=None)
                        .isoformat(),
                        "observation_time_delta_minutes": round(delta_minutes, 2),
                        "temperature_c": round(nearest["temperature_c"], 4),
                        "relative_humidity_pct": (
                            round(nearest["relative_humidity_pct"], 2)
                            if nearest["relative_humidity_pct"] is not None
                            else None
                        ),
                        "wind_speed_ms": (
                            round(nearest["wind_speed_ms"], 4)
                            if nearest["wind_speed_ms"] is not None
                            else None
                        ),
                        "source": config["observation_source"],
                    }
                )
    output.sort(key=lambda row: (row["target_time_local"], row["station_id"]))
    return output


def write_observations(path: Path, observations: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OBSERVATION_COLUMNS)
        writer.writeheader()
        writer.writerows(observations)


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * radius_m * math.asin(math.sqrt(a))


def feature_centroid(feature: dict[str, Any]) -> tuple[float, float]:
    ring = feature["geometry"]["coordinates"][0]
    points = ring[:-1] if ring and ring[0] == ring[-1] else ring
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def nearest_temperature_tile(
    result: dict[str, Any], longitude: float, latitude: float
) -> dict[str, Any] | None:
    features = result.get("map_data", {}).get("features", [])
    candidates: list[tuple[float, dict[str, Any], float, float]] = []
    for feature in features:
        temperature = feature.get("properties", {}).get("average_temperature")
        if temperature is None:
            continue
        tile_lon, tile_lat = feature_centroid(feature)
        candidates.append(
            (
                haversine_m(longitude, latitude, tile_lon, tile_lat),
                feature,
                tile_lon,
                tile_lat,
            )
        )
    if not candidates:
        return None
    distance, feature, tile_lon, tile_lat = min(candidates, key=lambda item: item[0])
    return {
        "temperature_c": float(feature["properties"]["average_temperature"]),
        "tile_id": str(feature.get("id", feature["properties"].get("tile_id", "unknown"))),
        "tile_longitude": tile_lon,
        "tile_latitude": tile_lat,
        "distance_m": distance,
    }


def cached_pair(
    observation: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any] | None:
    """Reuse any exact-hour committed heatmap with a tile close to the sensor."""

    target = datetime.fromisoformat(observation["target_time_local"])
    max_distance = config["fortyguard"]["max_station_to_tile_distance_m"]
    candidates: list[dict[str, Any]] = []
    for path in sorted(CACHE_DIR.glob("v1-heatmap__*.json")):
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        payload = raw.get("payload", {})
        date_time = payload.get("date_time", {})
        if payload.get("analytic_type") != config["fortyguard"]["analytic_type"]:
            continue
        if date_time.get("start_date") != target.date().isoformat():
            continue
        if date_time.get("start_time") != target.strftime("%H:%M"):
            continue
        tile = nearest_temperature_tile(
            raw.get("result", {}), observation["longitude"], observation["latitude"]
        )
        if tile is None or tile["distance_m"] > max_distance:
            continue
        candidates.append(
            _pair_from_tile(
                observation,
                tile,
                activity_id=raw.get("activity_id"),
                freshness="cached",
                cache_file=path,
            )
        )
    if not candidates:
        return None
    return min(candidates, key=lambda row: row["station_to_tile_distance_m"])


def station_aoi(station: dict[str, Any], half_size_m: float) -> dict[str, Any]:
    latitude = float(station["latitude"])
    longitude = float(station["longitude"])
    delta_lat = half_size_m / 111_320.0
    delta_lon = half_size_m / (111_320.0 * math.cos(math.radians(latitude)))
    ring = [
        [longitude - delta_lon, latitude - delta_lat],
        [longitude + delta_lon, latitude - delta_lat],
        [longitude + delta_lon, latitude + delta_lat],
        [longitude - delta_lon, latitude + delta_lat],
        [longitude - delta_lon, latitude - delta_lat],
    ]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"station_id": station["id"]},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        ],
    }


def expected_heatmap_payload(
    observation: dict[str, Any], station: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Recreate the exact request payload used by ``FortyGuardClient``."""

    target = datetime.fromisoformat(observation["target_time_local"])
    fg = config["fortyguard"]
    return {
        "polygon_aoi": station_aoi(station, fg["aoi_half_size_m"]),
        "date_time": {
            "start_date": target.date().isoformat(),
            "filter_type": 1,
            "start_time": target.strftime("%H:%M"),
        },
        "granularity": fg["granularity_m"],
        "analytic_type": fg["analytic_type"],
    }


def build_collection_outcomes(
    observations: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Account for every planned station-hour, including empty API results."""

    stations = {station["id"]: station for station in config["stations"]}
    pairs_by_key = {
        (pair["station_id"], pair["target_time_local"]): pair for pair in pairs
    }
    outcomes: list[dict[str, Any]] = []
    for observation in observations:
        key = (observation["station_id"], observation["target_time_local"])
        pair = pairs_by_key.get(key)
        if pair is not None:
            outcomes.append(
                {
                    "station_id": key[0],
                    "target_time_local": key[1],
                    "status": "paired",
                    "fortyguard_activity_id": pair["fortyguard_activity_id"],
                    "fortyguard_cache_file": pair["fortyguard_cache_file"],
                }
            )
            continue

        payload = expected_heatmap_payload(
            observation, stations[observation["station_id"]], config
        )
        cache_file = CACHE_DIR / f"{canonical_key('/v1/heatmap', payload)}.json"
        base = {
            "station_id": key[0],
            "target_time_local": key[1],
            "fortyguard_cache_file": cache_file.relative_to(ROOT).as_posix(),
        }
        if not cache_file.exists():
            outcomes.append({**base, "status": "not_collected"})
            continue
        try:
            raw = json.loads(cache_file.read_text())
        except (OSError, json.JSONDecodeError):
            outcomes.append({**base, "status": "cache_unreadable"})
            continue

        features = raw.get("result", {}).get("map_data", {}).get("features", [])
        temperature_tiles = [
            feature
            for feature in features
            if feature.get("properties", {}).get("average_temperature") is not None
        ]
        detail = {
            **base,
            "fortyguard_activity_id": raw.get("activity_id"),
            "feature_count": len(features),
            "temperature_tile_count": len(temperature_tiles),
        }
        if not features:
            status = "fortyguard_empty_heatmap"
        elif not temperature_tiles:
            status = "fortyguard_no_temperature_tiles"
        else:
            tile = nearest_temperature_tile(
                raw["result"], observation["longitude"], observation["latitude"]
            )
            detail["nearest_tile_distance_m"] = round(tile["distance_m"], 2)
            status = "fortyguard_tile_outside_distance_limit"
        outcomes.append({**detail, "status": status})

    outcomes.sort(key=lambda row: (row["target_time_local"], row["station_id"]))
    return outcomes


def _pair_from_tile(
    observation: dict[str, Any],
    tile: dict[str, Any],
    *,
    activity_id: str | None,
    freshness: str,
    cache_file: Path,
) -> dict[str, Any]:
    return {
        "station_id": observation["station_id"],
        "latitude": observation["latitude"],
        "longitude": observation["longitude"],
        "target_time_local": observation["target_time_local"],
        "fortyguard_temperature_c": round(tile["temperature_c"], 4),
        "fortyguard_activity_id": activity_id,
        "fortyguard_freshness": freshness,
        "fortyguard_tile_id": tile["tile_id"],
        "fortyguard_tile_longitude": round(tile["tile_longitude"], 7),
        "fortyguard_tile_latitude": round(tile["tile_latitude"], 7),
        "station_to_tile_distance_m": round(tile["distance_m"], 2),
        "fortyguard_cache_file": cache_file.relative_to(ROOT).as_posix(),
    }


async def live_pair(
    client: Any,
    observation: dict[str, Any],
    station: dict[str, Any],
    config: dict[str, Any],
    *,
    refresh: bool,
) -> dict[str, Any] | None:
    from thermcue.fortyguard.cache import canonical_key

    target = datetime.fromisoformat(observation["target_time_local"])
    fg = config["fortyguard"]
    response = await client.create_heatmap(
        polygon_aoi=station_aoi(station, fg["aoi_half_size_m"]),
        start_date=target.date().isoformat(),
        filter_type=1,
        granularity=fg["granularity_m"],
        start_time=target.strftime("%H:%M"),
        analytic_type=fg["analytic_type"],
        refresh=refresh,
    )
    tile = nearest_temperature_tile(
        response.result, observation["longitude"], observation["latitude"]
    )
    if tile is None or tile["distance_m"] > fg["max_station_to_tile_distance_m"]:
        return None
    payload = response.payload
    key = canonical_key(response.endpoint, payload)
    cache_file = CACHE_DIR / f"{key}.json"
    return _pair_from_tile(
        observation,
        tile,
        activity_id=response.activity_id,
        freshness=response.freshness,
        cache_file=cache_file,
    )


async def collect_pairs(
    observations: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    fetch_fortyguard: bool,
    refresh: bool,
    concurrency: int,
) -> list[dict[str, Any]]:
    stations = {station["id"]: station for station in config["stations"]}
    pairs: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for observation in observations:
        pair = cached_pair(observation, config)
        if pair is not None:
            pairs.append(pair)
        else:
            missing.append(observation)

    if fetch_fortyguard and missing:
        from thermcue.config import get_settings
        from thermcue.fortyguard import FortyGuardClient

        if not os.environ.get("FORTYGUARD_API_KEY"):
            raise RuntimeError(
                "--fetch-fortyguard requires FORTYGUARD_API_KEY. Existing committed "
                "responses were still reused; no missing value was fabricated."
            )
        settings = get_settings()
        if not settings.has_fortyguard_key:
            raise RuntimeError(
                "--fetch-fortyguard requires FORTYGUARD_API_KEY. Existing committed "
                "responses were still reused; no missing value was fabricated."
            )
        if concurrency < 1:
            raise ValueError("--concurrency must be at least 1")
        async with FortyGuardClient(settings=settings) as client:
            semaphore = asyncio.Semaphore(concurrency)
            completed = 0
            progress_lock = asyncio.Lock()

            async def fetch_one(observation: dict[str, Any]) -> dict[str, Any] | None:
                nonlocal completed
                async with semaphore:
                    pair = await live_pair(
                        client,
                        observation,
                        stations[observation["station_id"]],
                        config,
                        refresh=refresh,
                    )
                async with progress_lock:
                    completed += 1
                    print(
                        f"FortyGuard {completed}/{len(missing)} complete: "
                        f"{observation['station_id']} "
                        f"{observation['target_time_local']}"
                    )
                return pair

            fetched = await asyncio.gather(
                *(fetch_one(observation) for observation in missing)
            )
            pairs.extend(pair for pair in fetched if pair is not None)

    pairs.sort(key=lambda row: (row["target_time_local"], row["station_id"]))
    return pairs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--fetch-fortyguard",
        action="store_true",
        help="Make live FortyGuard calls for station-hours not already cached.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh live FortyGuard payloads instead of accepting exact cache hits.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Maximum simultaneous FortyGuard jobs (default: 4).",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    observations = download_observations(config)
    write_observations(OBSERVATIONS_PATH, observations)
    pairs = await collect_pairs(
        observations,
        config,
        fetch_fortyguard=args.fetch_fortyguard,
        refresh=args.refresh,
        concurrency=args.concurrency,
    )
    PAIRS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PAIRS_PATH.write_text(json.dumps(pairs, indent=2, sort_keys=True) + "\n")
    outcomes = build_collection_outcomes(observations, pairs, config)
    OUTCOMES_PATH.write_text(json.dumps(outcomes, indent=2, sort_keys=True) + "\n")
    unmatched_reasons = {
        (row["station_id"], row["target_time_local"]): row["status"]
        for row in outcomes
        if row["status"] != "paired"
    }
    report = build_observed_validation_report(
        config, observations, pairs, unmatched_reasons
    )
    write_observed_validation(REPORT_PATH, report)
    print(
        f"observations={len(observations)} pairs={report.paired_station_hours}/"
        f"{report.expected_station_hours} status={report.status}"
    )
    if report.fortyguard.n:
        print(
            f"FortyGuard MAE={report.fortyguard.mae_c:.3f} C "
            f"bias={report.fortyguard.bias_c:.3f} C"
        )
    if report.airport_baseline.n:
        print(
            f"Comparable FortyGuard MAE={report.fortyguard_comparable.mae_c:.3f} C; "
            f"KPHX baseline MAE={report.airport_baseline.mae_c:.3f} C over "
            f"{report.airport_baseline.n} comparable rows"
        )


if __name__ == "__main__":
    asyncio.run(main())
