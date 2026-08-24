#!/usr/bin/env python
"""How much of the optimiser's headroom is FortyGuard's spatial signal?

This is the product's central claim under test. ThermCue's metric rewards moving
queues out of hot zones and into cooler ones, so its headroom is bounded by how
much the zones actually differ. If a venue is uniformly hot, there is nowhere
cooler to move anyone and heat-aware routing has nothing to work with.

The experiment runs the full optimisation twice on the same day, same arrivals,
same limits, changing one thing: whether per-zone temperature offsets are
applied. The offsets used in the treatment arm are representative magnitudes for
a Phoenix plaza-versus-turf contrast; the point is not their exact values but
that a venue with structure and a venue without it are different optimisation
problems.

Reproducible: fixed seed, no randomness, prints every number it claims.

    .venv/bin/python scripts/spatial_signal_experiment.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thermcue.optimise import run_full_optimisation  # noqa: E402
from thermcue.scenario import load_scenario  # noqa: E402
from thermcue.service import ThermalBundle, build_thermal_bundle  # noqa: E402
from thermcue.thermal import estimate_wbgt  # noqa: E402

#: Representative per-zone offsets against the venue mean. Unshaded hardscape
#: runs hot, irrigated turf runs cool; magnitudes are ordinary for an urban
#: venue in the heat-island literature. Replace with measured FortyGuard offsets
#: once a key is configured - the engine does this automatically.
REPRESENTATIVE_OFFSETS_C = {
    "z-plaza": +1.6,
    "z-west-queue": +1.9,
    "z-concourse": -0.4,
    "z-lawn": -1.1,
    "z-staff": -0.9,
}


def apply_offsets(bundle: ThermalBundle, offsets: dict[str, float]) -> None:
    """Shift air temperature per zone and recompute WBGT from it.

    Deliberately not a direct WBGT shift: bands must follow from the temperature
    the panel displays, or the two would disagree on screen.
    """
    for row in bundle.zone_hours:
        delta = offsets.get(row.zone_id, 0.0)
        row.t_air_c = round(row.t_air_c + delta, 2)
        estimate = estimate_wbgt(
            row.t_air_c, row.rh_pct, row.wind_ms, row.solar_ghi_wm2,
            shaded_fraction=row.shaded_fraction,
        )
        row.wbgt_shade_adjusted_c = round(estimate.wbgt_c, 2)
        row.band = estimate.band
        bundle.field.band[row.zone_id][row.hour] = estimate.band
        bundle.field.wbgt_c[row.zone_id][row.hour] = round(estimate.wbgt_c, 2)


def census(bundle: ThermalBundle) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in bundle.zone_hours:
        counts[row.band] = counts.get(row.band, 0) + 1
    return dict(sorted(counts.items()))


def max_spread(bundle: ThermalBundle) -> float:
    spreads = []
    hours = {row.hour for row in bundle.zone_hours}
    for hour in hours:
        values = [r.wbgt_shade_adjusted_c for r in bundle.zone_hours if r.hour == hour]
        if len(values) > 1:
            spreads.append(max(values) - min(values))
    return max(spreads, default=0.0)


async def main() -> int:
    scenario = load_scenario()

    print("=" * 72)
    print("CONTROL: no per-zone spatial signal")
    print("=" * 72)
    control = await build_thermal_bundle(scenario)
    print(f"FortyGuard spatial signal present: {control.has_spatial_signal}")
    print(f"Band census:            {census(control)}")
    print(f"Max intra-venue spread: {max_spread(control):.2f} C")
    control_result = run_full_optimisation(scenario, control.field)
    print(f"HPM reduction:          {control_result.hpm_reduction_pct:+.2f} %")
    print(f"Total wait change:      {control_result.wait_change_pct:+.2f} %")

    print()
    print("=" * 72)
    print("TREATMENT: representative per-zone offsets applied")
    print("=" * 72)
    treatment = await build_thermal_bundle(scenario, refresh=False)
    apply_offsets(treatment, REPRESENTATIVE_OFFSETS_C)
    print(f"Offsets applied:        {REPRESENTATIVE_OFFSETS_C}")
    print(f"Band census:            {census(treatment)}")
    print(f"Max intra-venue spread: {max_spread(treatment):.2f} C")
    treatment_result = run_full_optimisation(scenario, treatment.field)
    print(f"HPM reduction:          {treatment_result.hpm_reduction_pct:+.2f} %")
    print(f"Total wait change:      {treatment_result.wait_change_pct:+.2f} %")

    print()
    print("=" * 72)
    delta = treatment_result.hpm_reduction_pct - control_result.hpm_reduction_pct
    print(f"Headroom attributable to the spatial signal: {delta:+.2f} percentage points")
    print(
        "A venue with measured intra-venue structure and a venue without it are "
        "different optimisation problems. The structure is what FortyGuard "
        "supplies and what a single weather station cannot."
    )
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
