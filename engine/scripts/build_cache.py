#!/usr/bin/env python
"""Populate the disk cache so the demo serves with the network removed.

The judging requirement is a public demo that works with zero setup. This script
makes every call the engine will make during a demo, once, so that afterwards
``THERMCUE_OFFLINE=1`` serves the whole application from disk.

Run before deploying, and again after any scenario change:

    FORTYGUARD_API_KEY=... .venv/bin/python scripts/build_cache.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thermcue.config import get_settings  # noqa: E402
from thermcue.optimise import run_full_optimisation  # noqa: E402
from thermcue.scenario import load_scenario  # noqa: E402
from thermcue.service import build_thermal_bundle  # noqa: E402
from thermcue.validation import build_validation  # noqa: E402


async def main() -> int:
    settings = get_settings()
    scenario = load_scenario()
    print(f"Scenario: {scenario.venue} on {scenario.date}")
    print(f"FortyGuard key configured: {settings.has_fortyguard_key}")

    bundle = await build_thermal_bundle(scenario, settings, refresh=True)
    print(f"Thermal bundle: {len(bundle.zone_hours)} zone-hours, freshness={bundle.freshness}")
    print(f"FortyGuard spatial signal: {bundle.has_spatial_signal}")

    validation = await build_validation(scenario, bundle)
    print(f"Validation: {len(validation.points)} points, "
          f"max spread {validation.summary.max_intra_venue_spread_c} C")

    result = run_full_optimisation(scenario, bundle.field)
    print(f"Optimisation: HPM {result.hpm_reduction_pct:+.2f}%, wait {result.wait_change_pct:+.2f}%")

    cached = sorted(Path(settings.cache_dir).glob("*.json"))
    print(f"\nCache now holds {len(cached)} entries in {settings.cache_dir}")
    for path in cached:
        print(f"  {path.name}  {path.stat().st_size / 1024:.0f} KB")
    print("\nVerify the fallback with: THERMCUE_OFFLINE=1 .venv/bin/python scripts/build_cache.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
