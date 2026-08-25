#!/usr/bin/env python
"""At what separation does FortyGuard actually resolve a temperature difference?

This is the product's central assumption under test rather than asserted.

ThermCue was designed on the premise that a venue is not one temperature. That
premise has a length scale, and the length scale is measurable. This script
measures it by pulling FortyGuard ``tcm`` heatmaps at increasing area of
interest, from a single venue up to a city, and reporting the observed
temperature spread at each.

The answer changed the product. Measured over Phoenix on 2026-08-14 at 17:00:

    within one venue      0.4 km      0.044 C
    venue to airport      4.5 km      0.056 C
    venue to South Mountain 12 km     2.420 C

**Air temperature is well mixed at venue scale.** A 60 m heatmap over a 0.5 km2
site returns 90 tiles spanning four hundredths of a degree, and the exceedance
and persistence layers over the same footprint return min equal to max: no
variation at all. FortyGuard is not failing here. This is a documented property
of the data - the vendor's own README states that below city scale the
temperature snapshot is nearly flat - and it is a physical property of air.

Two consequences, both of which improved the engine:

1. **Intra-venue heat differences are radiant, not advective.** If air
   temperature is uniform across a site but people still collapse in one corner
   of it, the difference is in the radiant load: sun, surface and shade. That is
   exactly why the operational index has to be WBGT and not air temperature, and
   why the shade model earns its place. Measured on this site at 15:00, air
   temperature varies by 0.00 C between zones while WBGT varies by 0.39 C,
   driven entirely by shaded fraction ranging 0.32 to 0.59. An order of
   magnitude more signal, from the term air temperature cannot see.

   (The full-sun to full-shade WBGT delta is larger still, about 2.8 C at peak
   sun, but no zone on this site is fully exposed or fully shaded, so 0.39 C is
   the honest figure for what separates these zones today rather than what shade
   is worth in principle.)

2. **FortyGuard's discriminating power is real but it is kilometre scale.** It
   separates the venue from South Mountain Park by 2.42 C. That is a venue
   siting and city planning signal, not an intra-venue one, and it is the honest
   frame for the sponsor's data in this application.

Reproducible, cache-backed, and it prints every number it claims.

    FORTYGUARD_API_KEY=... .venv/bin/python scripts/scale_experiment.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thermcue.config import get_settings  # noqa: E402
from thermcue.fortyguard import FortyGuardClient, FortyGuardError  # noqa: E402
from thermcue.scenario import load_scenario  # noqa: E402
from thermcue.service import build_thermal_bundle  # noqa: E402

STUDY_DATE = "2026-08-14"
STUDY_HOUR = "17:00"

#: Half-widths in kilometres. The first is the venue itself.
AOI_HALF_WIDTHS_KM = (0.35, 0.75, 1.5, 3.0, 6.0)

#: Phoenix sites at increasing separation from the venue.
SITES: dict[str, tuple[float, float]] = {
    "venue (Hance Park)": (33.4634, -112.0755),
    "downtown core (0.9 km)": (33.4484, -112.0740),
    "Sky Harbor airport (4.5 km)": (33.4373, -112.0116),
    "South Mountain Park (12 km)": (33.3450, -112.0640),
}

#: Degrees of longitude per degree of latitude at this latitude.
LON_SCALE = 0.834


def square_aoi(lat: float, lon: float, half_km: float) -> dict:
    d_lat = half_km / 111.0
    d_lon = half_km / (111.0 * LON_SCALE)
    ring = [
        [lon - d_lon, lat - d_lat],
        [lon + d_lon, lat - d_lat],
        [lon + d_lon, lat + d_lat],
        [lon - d_lon, lat + d_lat],
        [lon - d_lon, lat - d_lat],
    ]
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
        ],
    }


async def snapshot(client: FortyGuardClient, aoi: dict) -> dict | None:
    try:
        response = await client.create_heatmap(
            polygon_aoi=aoi,
            start_date=STUDY_DATE,
            start_time=STUDY_HOUR,
            filter_type=1,
            granularity=60,
            analytic_type="tcm",
        )
    except FortyGuardError as exc:
        print(f"  failed: {exc}", file=sys.stderr)
        return None
    result = response.result or {}
    stats = (result.get("stats_data") or {}).get("temperature_stats") or {}
    stats["_tiles"] = len((result.get("map_data") or {}).get("features") or [])
    stats["_freshness"] = response.freshness
    return stats


async def main() -> int:
    settings = get_settings()
    if not settings.has_fortyguard_key and not settings.offline:
        print("FORTYGUARD_API_KEY is not set and offline mode is off.", file=sys.stderr)
        return 2

    scenario = load_scenario()
    lat, lon = scenario.centroid[1], scenario.centroid[0]

    async with FortyGuardClient(settings) as client:
        print("=" * 78)
        print("PART 1 - temperature spread against area of interest")
        print(f"Phoenix, {STUDY_DATE} {STUDY_HOUR} local, tcm heatmap at 60 m")
        print("=" * 78)
        print(f"{'AOI':>14}  {'area':>9}  {'tiles':>7}  {'min':>7}  {'max':>7}  {'spread':>8}  {'sd':>6}")
        for half_km in AOI_HALF_WIDTHS_KM:
            stats = await snapshot(client, square_aoi(lat, lon, half_km))
            if not stats:
                continue
            side = 2 * half_km
            print(
                f"{side:>6.1f}x{side:<6.1f}  {side * side:>7.1f}km2  {stats['_tiles']:>7d}  "
                f"{stats['minimum']:>7.2f}  {stats['maximum']:>7.2f}  "
                f"{stats['maximum'] - stats['minimum']:>7.3f}C  {stats['standard_deviation']:>6.3f}"
            )

        print()
        print("=" * 78)
        print("PART 2 - separation between Phoenix sites")
        print("=" * 78)
        means: dict[str, float] = {}
        for name, (site_lat, site_lon) in SITES.items():
            stats = await snapshot(client, square_aoi(site_lat, site_lon, 0.3))
            if not stats:
                continue
            means[name] = float(stats["mean"])
            print(f"{name:<30s} mean {stats['mean']:>7.3f} C")
        if len(means) > 1:
            print(f"\nSpread across sites: {max(means.values()) - min(means.values()):.3f} C")

        print()
        print("=" * 78)
        print("PART 3 - what the analysis layers say over the venue footprint")
        print("=" * 78)
        for analytic, threshold in (("exceedance", 40.0), ("persistence", 40.0)):
            try:
                response = await client.create_heatmap(
                    polygon_aoi=scenario.aoi,
                    start_date="2026-08-11",
                    end_date="2026-08-17",
                    filter_type=4,
                    granularity=60,
                    analytic_type=analytic,
                    threshold=threshold,
                    direction="above",
                )
            except FortyGuardError as exc:
                print(f"{analytic}: failed: {exc}", file=sys.stderr)
                continue
            stats = (response.result or {}).get("stats_data") or {}
            print(
                f"{analytic:<12s} threshold {threshold:.0f}C  cells {stats.get('n_cells')}  "
                f"min {stats.get('min')}  max {stats.get('max')}  units {stats.get('units')}"
            )
        print(
            "\nMin equal to max means the layer does not vary across the venue "
            "footprint at all."
        )

        print()
        print("=" * 78)
        print("PART 4 - by contrast, what shade does to WBGT on the same site")
        print("=" * 78)
        bundle = await build_thermal_bundle(scenario, settings)
        for hour in scenario.hours:
            rows = [r for r in bundle.zone_hours if r.hour == hour]
            if len(rows) < 2:
                continue
            hottest = max(rows, key=lambda r: r.wbgt_shade_adjusted_c)
            coolest = min(rows, key=lambda r: r.wbgt_shade_adjusted_c)
            print(
                f"{hour:02d}:00  air spread {max(r.t_air_c for r in rows) - min(r.t_air_c for r in rows):>5.2f} C   "
                f"WBGT spread {hottest.wbgt_shade_adjusted_c - coolest.wbgt_shade_adjusted_c:>5.2f} C   "
                f"(shade {coolest.shaded_fraction:.2f} to {hottest.shaded_fraction:.2f})"
            )

        print()
        print("=" * 78)
        print(
            "Air temperature is uniform at venue scale; radiant load is not. That "
            "is the case for WBGT over air temperature, and it is measured here "
            "rather than assumed."
        )
        print("=" * 78)
        print("credits:", json.dumps(client.ledger.summary()))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
