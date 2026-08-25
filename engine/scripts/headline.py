#!/usr/bin/env python
"""Regenerate the headline results table, with the timestamp it was measured at.

Every figure ThermCue reports depends on a live forecast for an event four days
out, and that forecast moves. It moved 2 C cooler during a single day of
development, which took the venue from five High-band zone-hours to none and cut
heat-weighted exposure by a factor of four.

That is the product behaving correctly, and it is exactly what the agent's
replanning trigger exists for. But it means a number quoted in a README without a
timestamp is false within hours. So the README quotes this file, this file
carries the moment it was measured and the forecast it was measured against, and
this script regenerates both.

    .venv/bin/python scripts/headline.py

Add --pin to also freeze the current cache as the demo baseline, so the deployed
demo and the quoted numbers cannot drift apart.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thermcue.optimise import run_full_optimisation  # noqa: E402
from thermcue.scenario import load_scenario  # noqa: E402
from thermcue.service import build_thermal_bundle  # noqa: E402
from thermcue.simulate import HEADLINE_SEED, weight_sensitivity  # noqa: E402
from thermcue.validation import build_validation  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent.parent / "docs"


async def main() -> int:
    scenario = load_scenario()
    bundle = await build_thermal_bundle(scenario)
    result = run_full_optimisation(scenario, bundle.field)
    validation = await build_validation(scenario, bundle)
    sensitivity = weight_sensitivity(
        scenario, result.baseline.plan, result.optimised.plan, bundle.field, HEADLINE_SEED
    )

    measured_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # Event window only. The forecast's 00:00 value can be a day-boundary
    # artefact and reporting it as "peak" would describe an hour nobody attends.
    event_hours = [h for h in bundle.venue.hours if h.hour in scenario.hours]
    peak = max(event_hours or list(bundle.venue.hours), key=lambda h: h.t_air_c)
    bands: dict[str, int] = {}
    for row in bundle.zone_hours:
        bands[row.band] = bands.get(row.band, 0) + 1

    lines: list[str] = [
        "# Headline results",
        "",
        f"Measured **{measured_at}** by `engine/scripts/headline.py`, seed "
        f"`{HEADLINE_SEED}`.",
        "",
        "> These figures depend on a live forecast for an event four days out, and "
        "that forecast moves. Regenerate with `.venv/bin/python scripts/headline.py` "
        "rather than trusting a stale copy.",
        "",
        "## Conditions the plan was built on",
        "",
        "| | |",
        "|---|---|",
        f"| Event | {scenario.event_name}, {scenario.venue} |",
        f"| Date | {scenario.date}, {scenario.start_hour:02d}:00 to "
        f"{scenario.end_hour:02d}:00 {scenario.timezone} |",
        f"| Peak forecast air temperature | {peak.t_air_c:.1f} C at {peak.hour:02d}:00 |",
        f"| Data freshness | {bundle.freshness} |",
        f"| FortyGuard spatial signal | {'applied' if bundle.has_spatial_signal else 'absent'} |",
        f"| Analogue day | {bundle.analogue.date if bundle.analogue else 'n/a'}"
        + (
            f", RMS {bundle.analogue.rms_error_c:.2f} C, {bundle.analogue.quality} match |"
            if bundle.analogue
            else " |"
        ),
        f"| Band census across zone-hours | "
        + ", ".join(f"{k} {v}" for k, v in sorted(bands.items()))
        + " |",
        "",
        "## Plan comparison",
        "",
        "| | Baseline | ThermCue plan | Change |",
        "|---|---:|---:|---:|",
        f"| Heat-weighted person-minutes | {result.baseline.hpm:,.0f} | "
        f"{result.optimised.hpm:,.0f} | **{-result.hpm_reduction_pct:+.1f} %** |",
        f"| Person-minutes in High/Extreme | "
        f"{result.baseline.result.person_minutes_high_extreme:,.0f} | "
        f"{result.optimised.result.person_minutes_high_extreme:,.0f} | |",
        f"| Total wait (person-minutes) | {result.baseline.total_wait:,.0f} | "
        f"{result.optimised.total_wait:,.0f} | **{result.wait_change_pct:+.1f} %** |",
        f"| Longest single wait | {result.baseline.result.longest_wait_minutes:,.0f} min | "
        f"{result.optimised.result.longest_wait_minutes:,.0f} min | |",
        "",
        f"Candidate plans simulated: **{result.candidates_evaluated:,}**.",
        "",
        "## Changes and their counterfactual shares",
        "",
        "| Share | Change |",
        "|---:|---|",
    ]
    for change in result.changes:
        lines.append(f"| {change.counterfactual_share_pct:.1f} % | {change.action} |")
    for move in result.resource_moves:
        lines.append(
            f"| relief | Relocate {move['resource_name']} from {move['from_zone']} "
            f"to {move['to_zone']} |"
        )

    lines += [
        "",
        "## Metric defence: does the plan still win under other band weightings?",
        "",
        "| Weighting | Baseline HPM | Plan HPM | Reduction | Plan wins |",
        "|---|---:|---:|---:|---|",
    ]
    for name, row in sensitivity.items():
        lines.append(
            f"| {name} | {row['baseline_hpm']:,.0f} | {row['optimised_hpm']:,.0f} | "
            f"{row['hpm_reduction_pct']:.2f} % | {'yes' if row['optimised_wins'] else 'NO'} |"
        )

    # Four identical rows are not four independent confirmations. When only Low
    # and Moderate bands occur, every weighting variant assigns those the same
    # 0 and 1, so the table is arithmetically bound to agree and proves nothing.
    # Saying so is the difference between evidence and false reassurance.
    exercised = {b for b in bands if b in ("high", "extreme")}
    if not exercised:
        lines += [
            "",
            "> **This table is vacuous on the current forecast.** No zone-hour "
            "reaches the High or Extreme band, and all four weighting variants "
            "assign Low and Moderate the same weights of 0 and 1, so identical "
            "results are arithmetic rather than confirmation. The sensitivity "
            "check only carries evidence on a forecast that reaches the upper "
            "bands.",
        ]

    lines += [
        "",
        "## Validation against the single station",
        "",
        f"- Maximum intra-venue air-temperature spread: "
        f"**{validation.summary.max_intra_venue_spread_c} C**",
        f"- Zone-hours where the venue and {validation.station_name} disagree on band: "
        f"**{len(validation.disagreements)}**",
        "",
        f"> {validation.summary.verdict_decision}",
        "",
        "## Provenance",
        "",
    ]
    for field, origin in bundle.sources.items():
        lines.append(f"- `{field}`: {origin}")
    lines += ["", "## Stated limits carried with these numbers", ""]
    for note in bundle.notes + result.notes:
        lines.append(f"- {note}")

    DOCS.mkdir(parents=True, exist_ok=True)
    target = DOCS / "headline.md"
    target.write_text("\n".join(lines) + "\n")

    print("\n".join(lines))
    print(f"\nWrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
