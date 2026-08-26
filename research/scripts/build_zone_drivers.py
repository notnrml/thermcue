#!/usr/bin/env python
"""Build research/zone_heat_drivers.json from FortyGuard satellite segmentation.

Workstream 3's handoff artefact. The engine consumes it in
``thermcue.shade.load_zone_drivers`` and the UI's Drivers tab renders it; without
it every zone reads "Evidence unavailable", which is honest but empty, and the
API test asserting driver evidence fails.

Everything here comes from a Premium FortyGuard endpoint. Nothing is authored:
the land-cover fractions are exactly what ``POST /v1/satellite`` returned for
each zone centroid, and the derived figures are stated arithmetic over them.

The limitation that matters, and it is recorded in the artefact itself
-------------------------------------------------------------------
The segmentation crop is 225x225 px around a point, which at this venue covers
far more ground than one zone. Two zone centroids 350 m apart returned 82.9 %
and 82.0 % building - not because both zones are nearly all building, but
because both crops are dominated by the same downtown blocks. So these fractions
describe **the district around each zone**, not the zone itself, and the
per-zone differences are weaker evidence than they look.

That is why ``driver_score`` is published as a structural exposure index and the
narratives talk about surfaces rather than temperatures. An earlier draft of the
brief's example narrative said a zone "runs 2.1 C above venue mean"; we measured
the actual intra-venue air-temperature spread at 0.044 C, so no narrative here
makes a temperature claim.

    FORTYGUARD_API_KEY=... python research/scripts/build_zone_drivers.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "engine"))

from thermcue.config import get_settings  # noqa: E402
from thermcue.fortyguard import FortyGuardClient, FortyGuardError  # noqa: E402
from thermcue.scenario import load_scenario  # noqa: E402

OUTPUT = REPO_ROOT / "research" / "zone_heat_drivers.json"

#: Study date and hour, matching the analogue day the engine pins.
STUDY_DATE = "2026-08-14"
STUDY_HOUR = "17:00"

#: Which segmentation classes count as which surface family. FortyGuard's label
#: set varies by tile, so unknown labels are counted in ``other_frac`` rather
#: than silently folded into one of these.
IMPERVIOUS = {"building", "house", "road, route", "fence", "wall", "skyscraper", "path"}
VEGETATION = {"tree", "grass", "plant", "field", "palm"}
STRUCTURE = {"building", "house", "skyscraper"}
"""Structures tall enough to cast usable shade. Roads and fences do not."""


def fractions(segments: dict[str, float]) -> dict[str, float]:
    """Normalise FortyGuard's percentage segments into surface families."""
    total = sum(segments.values()) or 1.0
    impervious = sum(v for k, v in segments.items() if k in IMPERVIOUS)
    vegetation = sum(v for k, v in segments.items() if k in VEGETATION)
    structure = sum(v for k, v in segments.items() if k in STRUCTURE)
    known = {k for k in segments if k in IMPERVIOUS or k in VEGETATION}
    other = sum(v for k, v in segments.items() if k not in known)
    return {
        "impervious_frac": round(impervious / total, 4),
        "vegetation_frac": round(vegetation / total, 4),
        "shade_structure_frac": round(structure / total, 4),
        "other_frac": round(other / total, 4),
    }


def driver_score(f: dict[str, float]) -> float:
    """Structural heat exposure, 0 to 1.

    Impervious surface stores and re-radiates heat; vegetation cools by
    evapotranspiration and shading. The score is the impervious share net of
    vegetation, clamped. It is an index over surface composition, **not** a
    temperature and not a forecast, and the artefact says so beside every value.
    """
    return round(min(max(f["impervious_frac"] - f["vegetation_frac"], 0.0), 1.0), 4)


def narrative(zone_name: str, f: dict[str, float], score: float) -> str:
    """One sentence about surfaces. Never about degrees."""
    impervious = f["impervious_frac"] * 100
    vegetation = f["vegetation_frac"] * 100
    if score >= 0.7:
        lead = f"Dominated by impervious surface ({impervious:.0f} %)"
    elif score >= 0.4:
        lead = f"Mostly impervious ({impervious:.0f} %) with some relief"
    else:
        lead = f"Mixed surface, {impervious:.0f} % impervious"
    return (
        f"{lead} against {vegetation:.0f} % vegetation, from FortyGuard satellite "
        f"segmentation on {STUDY_DATE}. Describes the district around {zone_name}; "
        f"the crop is wider than the zone."
    )


async def main() -> int:
    settings = get_settings()
    scenario = load_scenario()

    if not settings.has_fortyguard_key and not settings.offline:
        print("FORTYGUARD_API_KEY is not set.", file=sys.stderr)
        return 2

    drivers: dict[str, dict] = {}
    async with FortyGuardClient(settings) as client:
        for zone in scenario.zones:
            try:
                response = await client.satellite_segmentation(
                    latitude=zone.centroid[1],
                    longitude=zone.centroid[0],
                    start_date=STUDY_DATE,
                    start_time=STUDY_HOUR,
                    filter_type=1,
                    granularity=60,
                )
            except FortyGuardError as exc:
                print(f"{zone.id}: segmentation failed: {exc}", file=sys.stderr)
                continue

            segmentation = (response.result or {}).get("segmentation") or {}
            segments = segmentation.get("segments") or {}
            if not segments:
                print(f"{zone.id}: no segments returned; skipped", file=sys.stderr)
                continue

            f = fractions(segments)
            score = driver_score(f)
            drivers[zone.id] = {
                **f,
                "driver_score": score,
                "narrative": narrative(zone.name, f, score),
                "segments_pct": segments,
                "source": "fortyguard:/v1/satellite",
                "study_date": STUDY_DATE,
                "study_hour": STUDY_HOUR,
                "freshness": response.freshness,
                "activity_id": response.activity_id,
            }
            print(
                f"{zone.id:14s} impervious {f['impervious_frac']:.2f} "
                f"vegetation {f['vegetation_frac']:.2f} score {score:.2f} "
                f"[{response.freshness}]"
            )

    if not drivers:
        print("No zones produced segmentation; artefact not written.", file=sys.stderr)
        return 3

    payload = {
        "_source": "FortyGuard POST /v1/satellite (Premium), land-cover segmentation",
        "_study_date": STUDY_DATE,
        "_study_hour": STUDY_HOUR,
        "_generated_by": "research/scripts/build_zone_drivers.py",
        "_limitations": [
            "The segmentation crop is 225x225 px around each zone centroid, which "
            "at this venue is wider than the zone. Two centroids 350 m apart "
            "returned 82.9 % and 82.0 % building because both crops are dominated "
            "by the same downtown blocks, so these fractions describe the district "
            "around a zone rather than the zone itself.",
            "driver_score is a structural exposure index over surface composition. "
            "It is not a temperature, not an anomaly and not a forecast.",
            "No narrative here makes a temperature claim. Measured intra-venue "
            "air-temperature spread at this venue is 0.044 C, so a per-zone "
            "degree claim would not be supportable.",
            "Segmentation is a single-date snapshot. Surfaces change slowly, but "
            "temporary event infrastructure is not in it.",
        ],
        "zones": drivers,
    }
    # The engine reads zone ids at the top level, so keep that shape and carry
    # the metadata alongside rather than nesting the zones a level deeper.
    flat = {zone_id: entry for zone_id, entry in drivers.items()}
    flat["_meta"] = {k: v for k, v in payload.items() if k != "zones"}

    OUTPUT.write_text(json.dumps(flat, indent=2, sort_keys=True) + "\n")
    print(f"\nWrote {OUTPUT.relative_to(REPO_ROOT)} for {len(drivers)} zones")
    print(json.dumps(client.ledger.summary()))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
