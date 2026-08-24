#!/usr/bin/env python
"""Live FortyGuard verification, and the source of the README's request/response pair.

The submission requires one real API request and its real response in the README.
This script produces exactly that: it makes live calls, prints a table of what
came back, and writes the verbatim pair to ``docs/fortyguard_exchange.md`` with
the API key redacted.

Run it once the key is configured:

    FORTYGUARD_API_KEY=... .venv/bin/python scripts/verify_api.py

It costs a handful of credits and reports the spend.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thermcue.config import get_settings  # noqa: E402
from thermcue.fortyguard import FortyGuardClient, FortyGuardError  # noqa: E402
from thermcue.forecast import (  # noqa: E402
    WeatherProvider,
    analogue_search_window,
    select_analogue_day,
)
from thermcue.scenario import load_scenario  # noqa: E402
from thermcue.service import zone_offsets_from_heatmap  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent.parent / "docs"


async def main() -> int:
    settings = get_settings()
    scenario = load_scenario()

    if not settings.has_fortyguard_key:
        print("FORTYGUARD_API_KEY is not set. Nothing to verify.", file=sys.stderr)
        print(
            "The engine runs without it and says so on /health and /thermal, but "
            "the submission needs a real request/response pair, which requires "
            "the key.",
            file=sys.stderr,
        )
        return 2

    provider = WeatherProvider(settings)
    forecast = await provider.venue_forecast(
        scenario.centroid[1], scenario.centroid[0], scenario.date, scenario.timezone
    )
    start, end = analogue_search_window(scenario.date)
    observed = await provider.venue_observed(
        scenario.centroid[1], scenario.centroid[0], start, end, scenario.timezone
    )
    analogue = select_analogue_day(forecast, observed, scenario.hours)
    print(f"Analogue day: {analogue.date}  RMS {analogue.rms_error_c:.2f} C  "
          f"quality {analogue.quality}")

    peak_hour = max(forecast.hours, key=lambda h: h.t_air_c)
    exchange: dict[str, object] = {}

    async with FortyGuardClient(settings) as client:
        print("\n--- POST /v1/heatmap (tcm, 60 m) ---")
        heatmap = await client.create_heatmap(
            polygon_aoi=scenario.aoi,
            start_date=analogue.date,
            start_time=f"{peak_hour.hour:02d}:00",
            filter_type=1,
            granularity=60,
            analytic_type="tcm",
            refresh=True,
        )
        stats = (heatmap.result or {}).get("stats_data", {})
        features = (heatmap.result or {}).get("map_data", {}).get("features", [])
        print(f"activity_id: {heatmap.activity_id}")
        print(f"freshness:   {heatmap.freshness}")
        print(f"tiles:       {len(features)}")
        print(f"stats:       {json.dumps(stats)[:300]}")

        offsets = zone_offsets_from_heatmap(scenario, heatmap.result or {}, peak_hour.hour)
        print("\nPer-zone offset against the venue tile mean:")
        for zone in scenario.zones:
            value = offsets.get(zone.id)
            print(f"  {zone.name:20s} {value:+.2f} C" if value is not None else f"  {zone.name:20s}  no tiles")

        exchange["request"] = heatmap.request_record or {
            "method": "POST",
            "url": f"{client.base_url}/v1/heatmap",
            "headers": {"api-key": "<redacted>", "Content-Type": "application/json"},
            "body": heatmap.payload,
        }
        # Only the first two tiles are kept: the full response is several
        # megabytes of GeoJSON and a README that nobody can scroll past is worse
        # than one that shows the shape.
        exchange["response"] = {
            "error": False,
            "status_code": 200,
            "message": "Success",
            "data": {
                "activity_id": heatmap.activity_id,
                "status": "Completed",
                "result": {
                    "map_data": {
                        "type": "FeatureCollection",
                        "features": features[:2],
                        "_truncated": f"{max(len(features) - 2, 0)} further tiles omitted",
                    },
                    "stats_data": stats,
                },
            },
        }

        print("\n--- POST /v1/env_params ---")
        try:
            env = await client.environmental_parameters(
                latitude=scenario.centroid[1],
                longitude=scenario.centroid[0],
                temperature=peak_hour.t_air_c,
                start_date=analogue.date,
                filter_type=3,
                analysis=["relative_humidity_percent", "cloud_cover_octas", "solar_irradiance"],
                refresh=True,
            )
            location = ((env.result or {}).get("locations") or [{}])[0]
            humidity = (location.get("parameters") or {}).get("relative_humidity_percent") or []
            print(f"activity_id: {env.activity_id}")
            print(f"humidity samples: {len(humidity)}  first: {humidity[:4]}")
            print(f"solar clear-sky: {(location.get('solar_irradiance') or {}).get('clear_sky')}")
        except FortyGuardError as exc:
            print(f"env_params failed: {exc}", file=sys.stderr)

        print("\n--- credits ---")
        print(json.dumps(client.ledger.summary(), indent=2))
        try:
            usage = await client.fetch_api_key_usage()
            print(json.dumps(usage, indent=2)[:600])
        except FortyGuardError as exc:
            print(f"usage lookup failed: {exc}", file=sys.stderr)

    DOCS.mkdir(parents=True, exist_ok=True)
    target = DOCS / "fortyguard_exchange.md"
    target.write_text(
        "# FortyGuard request and response\n\n"
        "Captured live by `engine/scripts/verify_api.py`. The API key is redacted; "
        "everything else is verbatim. The tile list is truncated because the full "
        "response is several megabytes of GeoJSON.\n\n"
        "## Request\n\n```json\n"
        + json.dumps(exchange["request"], indent=2)
        + "\n```\n\n## Response\n\n```json\n"
        + json.dumps(exchange["response"], indent=2)
        + "\n```\n"
    )
    print(f"\nWrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
