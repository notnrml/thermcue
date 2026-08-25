"""Shared fixtures.

The thermal field here is a fixed lookup rather than a live FortyGuard call, so
simulation and optimiser tests assert queue behaviour without a network and
without coupling to the science layer. ``tests/test_thermal.py`` covers the
science; these cover what the queue does with it.
"""

from __future__ import annotations

import pytest

from thermcue.plan import Plan
from thermcue.scenario import load_scenario
from thermcue.simulate import ThermalField


@pytest.fixture(scope="session")
def scenario():
    return load_scenario()


@pytest.fixture(scope="session")
def thermal(scenario):
    """A hot afternoon cooling into the evening, with the lawn worst.

    Deliberately not uniform: a flat field would let a broken band lookup pass
    every test, because every zone would weigh the same.
    """
    bands = {
        "z-plaza": {15: "high", 16: "high", 17: "high", 18: "moderate", 19: "moderate", 20: "low", 21: "low"},
        "z-concourse": {15: "moderate", 16: "moderate", 17: "moderate", 18: "low", 19: "low", 20: "low", 21: "low"},
        "z-lawn": {15: "extreme", 16: "extreme", 17: "high", 18: "high", 19: "moderate", 20: "low", 21: "low"},
        "z-west-queue": {15: "high", 16: "high", 17: "high", 18: "moderate", 19: "moderate", 20: "low", 21: "low"},
        "z-staff": {15: "moderate", 16: "moderate", 17: "low", 18: "low", 19: "low", 20: "low", 21: "low"},
    }
    return ThermalField(
        band=bands,
        wbgt_c={z: {h: 29.5 + 0.2 * i for i, h in enumerate(scenario.hours)} for z in bands},
    )


@pytest.fixture()
def baseline_plan(scenario):
    return Plan.baseline(scenario)


@pytest.fixture(scope="session")
def optimisation(scenario, thermal):
    """One full optimisation, shared across every test that inspects it.

    The search now explores time-windowed staff swaps as well as gate timing and
    staggering, which is what makes the result reproducible across machines - and
    it costs about 12,000 simulated plans. Nine tests were each paying for their
    own run, which took the suite from under five minutes to over thirteen. It is
    deterministic, so one run is exactly as good as nine, and a suite nobody waits
    for is a suite nobody runs.
    """
    from thermcue.optimise import run_full_optimisation

    return run_full_optimisation(scenario, thermal)
