"""API contract tests.

These assert the shape the TypeScript side consumes, because a field name that
drifts renders as ``undefined`` in a chart rather than raising anywhere. The
network-dependent pipeline is exercised through the real app; the tests that
would otherwise need FortyGuard assert on degradation behaviour instead, which
is the state the judging deployment may well be in.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from thermcue.api import app


@pytest.fixture(scope="module", autouse=True)
def no_live_model():
    """Pin the API tests to the agent's deterministic path.

    Once a real model key sits in engine/.env these tests start driving a live
    provider: slow, metered, and non-deterministic, so a rate limit on someone
    else's account turns into a red build here. The model path is covered by
    tests/test_agent_providers.py against a mocked provider, which is where it
    belongs. These tests are about the HTTP contract.
    """
    import os

    from thermcue.config import get_settings

    saved = {
        k: os.environ.pop(k, None)
        for k in (
            "THERMCUE_LLM_API_KEY",
            "THERMCUE_LLM_PROVIDER",
            "THERMCUE_LLM_BASE_URL",
            "ANTHROPIC_API_KEY",
            "THERMCUE_AGENT_MODEL",
        )
    }
    os.environ["THERMCUE_LLM_API_KEY"] = ""
    os.environ["ANTHROPIC_API_KEY"] = ""
    get_settings.cache_clear()
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestHealth:
    def test_health_reports_which_sources_are_configured(self, client):
        """A demo that is up but keyless is a different state from a working one,
        and the difference must be visible without reading logs."""
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert "fortyguard_key_configured" in body
        assert "anthropic_key_configured" in body
        assert "offline_mode" in body


class TestScenario:
    def test_scenario_uses_the_typescript_field_names(self, client):
        scenario = client.get("/scenario").json()["scenario"]
        for key in ("timeWindow", "dataFreshness", "zones", "gates", "resources"):
            assert key in scenario, key
        assert {"startHour", "endHour"} <= set(scenario["timeWindow"])

    def test_zone_polygons_are_closed_rings_in_lng_lat_order(self, client):
        for zone in client.get("/scenario").json()["scenario"]["zones"]:
            ring = zone["polygon"]
            assert ring[0] == ring[-1]
            for lng, lat in ring:
                assert -180 <= lng <= 180 and -90 <= lat <= 90
                assert lng < 0 and lat > 20, "venue must sit in the continental US"

    def test_gates_carry_the_fields_the_map_reads(self, client):
        for gate in client.get("/scenario").json()["scenario"]["gates"]:
            assert {"id", "name", "coordinates", "capacity", "lanes", "staffCount",
                    "queueLength", "waitTimeMinutes"} <= set(gate)

    def test_zone_driver_evidence_reaches_the_ui_contract(self, client):
        zones = {
            zone["id"]: zone
            for zone in client.get("/scenario").json()["scenario"]["zones"]
        }
        for zone_id in ("z-plaza", "z-concourse", "z-lawn"):
            assert 0.0 <= zones[zone_id]["driverScore"] <= 1.0
            assert "satellite view" in zones[zone_id]["driverNarrative"]
        for zone_id in ("z-west-queue", "z-staff"):
            assert zones[zone_id]["driverScore"] is None
            assert "No committed" in zones[zone_id]["driverNarrative"]


class TestThermal:
    def test_thermal_states_freshness_and_signal_presence(self, client):
        body = client.get("/thermal").json()
        assert body["freshness"] in ("live", "cached")
        assert isinstance(body["hasFortyguardSpatialSignal"], bool)
        assert body["notes"], "the payload must always explain its own provenance"

    def test_every_zone_hour_carries_a_band_and_a_wbgt(self, client):
        for row in client.get("/thermal").json()["zones"]:
            assert row["band"] in ("low", "moderate", "high", "extreme")
            assert isinstance(row["wbgtShadeAdjustedC"], (int, float))

    def test_missing_spatial_signal_is_declared_not_hidden(self, client):
        """The one degradation that matters. If FortyGuard is absent the payload
        must say the intra-venue structure is missing, because a flat venue
        rendered as though it were measured is the product lying about itself."""
        body = client.get("/thermal").json()
        if not body["hasFortyguardSpatialSignal"]:
            joined = " ".join(body["notes"]).lower()
            assert "spatial" in joined or "fortyguard" in joined

    def test_hourly_zone_states_match_the_ui_contract(self, client):
        for row in client.get("/thermal").json()["hourlyZoneStates"]:
            assert {"zoneId", "hour", "wbgtBand", "temperatureC", "shadeCoverage"} == set(row)


class TestSimulate:
    def test_simulate_returns_camel_case_queue_states(self, client):
        body = client.post("/simulate?plan=baseline&monte_carlo_n=10").json()
        assert body["queueStates"]
        assert {"gateId", "hour", "arrivals", "queueLength", "waitTimeMinutes", "personMinutes"} == set(
            body["queueStates"][0]
        )

    def test_monte_carlo_percentiles_are_ordered(self, client):
        mc = client.post("/simulate?plan=baseline&monte_carlo_n=10").json()["monteCarlo"]
        assert mc["hpmP10"] <= mc["hpmP50"] <= mc["hpmP90"]

    def test_headline_run_is_reproducible_from_a_seed(self, client):
        first = client.post("/simulate?plan=baseline&monte_carlo_n=10").json()
        second = client.post("/simulate?plan=baseline&monte_carlo_n=10").json()
        assert first["kpis"] == second["kpis"]
        assert first["seed"] == second["seed"]

    def test_an_unknown_plan_label_is_rejected(self, client):
        assert client.post("/simulate?plan=wishful").status_code == 422


class TestOptimise:
    def test_optimise_never_reports_a_worse_plan(self, client):
        body = client.post("/optimise").json()
        assert body["kpis"]["optimised"]["heatWeightedPersonMinutes"] <= (
            body["kpis"]["baseline"]["heatWeightedPersonMinutes"] + 1e-6
        )

    def test_wait_constraint_is_respected(self, client):
        body = client.post("/optimise").json()
        assert body["waitChangePct"] <= 10.0 + 1e-6

    def test_every_change_carries_a_full_why_trace(self, client):
        """The brief: no change ships without a populated why-object."""
        for change in client.post("/optimise").json()["planChanges"]:
            assert change["action"]
            assert change["whyTrace"]
            stages = {step["stage"] for step in change["whyTrace"]}
            assert "Binding condition" in stages
            assert "Effect" in stages

    def test_weight_sensitivity_is_reported(self, client):
        """A metric whose ranking flips under a plausible reweighting is not
        defensible, so the evidence ships with the result either way."""
        sensitivity = client.post("/optimise").json()["weightSensitivity"]
        assert "headline-0124" in sensitivity
        assert len(sensitivity) >= 4
        for row in sensitivity.values():
            assert "optimised_wins" in row

    def test_notes_state_the_architecture(self, client):
        joined = " ".join(client.post("/optimise").json()["notes"]).lower()
        assert "simulator judges" in joined


class TestPareto:
    def test_frontier_and_points_are_camel_case(self, client):
        body = client.get("/pareto").json()
        assert {"waitRatio", "totalWaitMinutes", "heatWeightedExposure"} <= set(
            body["frontier"][0]
        )
        assert {"id", "totalWaitMinutes", "heatWeightedExposure", "kind"} == set(
            body["points"][0]
        )

    def test_scatter_has_exactly_one_baseline_and_one_chosen(self, client):
        kinds = [p["kind"] for p in client.get("/pareto").json()["points"]]
        assert kinds.count("baseline") == 1
        assert kinds.count("chosen") == 1


class TestValidation:
    def test_validation_carries_a_generated_verdict(self, client):
        body = client.get("/validation").json()
        assert body["summary"]["verdictDecision"]
        assert body["stationName"]
        assert isinstance(body["summary"]["maxIntraVenueSpreadC"], (int, float))

    def test_points_compare_zone_against_station(self, client):
        for point in client.get("/validation").json()["points"]:
            assert {"hour", "zoneId", "zoneTempC", "stationTempC"} == set(point)

    def test_observed_validation_is_independent_and_explicitly_partial(self, client):
        """Measured station evidence must not be confused with derived WBGT."""
        response = client.get("/validation/observed")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("complete", "partial")
        assert body["expectedStationHours"] == 84
        assert body["observedStationHours"] == 84
        assert body["pairedStationHours"] == body["fortyguard"]["n"]
        assert body["comparableStationHours"] == body["fortyguardComparable"]["n"]
        assert body["comparableStationHours"] == body["airportBaseline"]["n"]
        assert "ASOS/METAR" in body["stationSource"]
        assert body["fortyguardSource"].startswith("FortyGuard /v1/heatmap")
        joined = " ".join(body["limitations"]).lower()
        assert "does not validate estimated wbgt" in joined

    def test_observed_pairs_carry_raw_provenance(self, client):
        for pair in client.get("/validation/observed").json()["pairs"]:
            assert pair["observationTimeLocal"]
            assert pair["fortyguardActivityId"]
            assert pair["fortyguardCacheFile"].startswith("engine/data/cache/")
            assert pair["stationToTileDistanceM"] <= 150


class TestPlanWorkspace:
    def test_plan_returns_the_whole_ui_contract_in_one_call(self, client):
        body = client.get("/plan").json()
        expected = {
            "scenario", "hourlyZoneStates", "queueStates", "kpis", "paretoPoints",
            "planChanges", "agentFeed", "validationPoints", "validationSummary",
            "wbgtHourly",
        }
        assert expected <= set(body)

    def test_plan_carries_measured_validation_summary_separately(self, client):
        report = client.get("/plan").json()["observedValidation"]
        assert report["status"] in ("complete", "partial")
        assert report["observedStationHours"] == report["expectedStationHours"]
        assert report["comparableStationHours"] == report["fortyguardComparable"]["n"]
        assert report["comparableStationHours"] == report["airportBaseline"]["n"]

    def test_meta_carries_provenance_and_the_seed(self, client):
        meta = client.get("/plan").json()["meta"]
        assert meta["seed"]
        assert meta["sources"]
        assert meta["notes"]

    def test_wbgt_hourly_envelope_is_ordered(self, client):
        for row in client.get("/plan").json()["wbgtHourly"]:
            assert row["p10"] <= row["p50"] <= row["p90"]
            assert row["venueMax"] >= row["p90"] - 1e-6


class TestExports:
    def test_pdf_is_a_real_pdf(self, client):
        response = client.get("/export/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")
        assert len(response.content) > 2000

    def test_ics_uses_crlf_and_declares_a_calendar(self, client):
        """RFC 5545 requires CRLF. Calendar clients fail silently when it is
        wrong, which is the worst way for an export to be broken."""
        response = client.get("/export/ics")
        assert response.status_code == 200
        text = response.text
        assert text.startswith("BEGIN:VCALENDAR\r\n")
        assert text.rstrip().endswith("END:VCALENDAR")
        assert "\r\n" in text

    def test_every_ics_event_is_well_formed(self, client):
        text = client.get("/export/ics").text
        assert text.count("BEGIN:VEVENT") == text.count("END:VEVENT")
        for line in text.split("\r\n"):
            if line.startswith("DTSTART:"):
                assert line.endswith("Z"), "timestamps must be UTC"


class TestAgent:
    def test_history_is_available_and_shaped_for_the_feed(self, client):
        body = client.get("/agent/history").json()
        assert "directives" in body
        for directive in body["directives"]:
            assert {"id", "timestamp", "type", "text", "toolTrace"} <= set(directive)

    def test_trigger_rejects_an_unknown_zone(self, client):
        assert client.post("/agent/trigger?zone_id=z-nowhere").status_code == 404

    def test_trigger_produces_a_traced_directive(self, client):
        """The brief's acceptance gate: a correct, fully traced autonomous
        replan. Trace completeness is what makes it auditable."""
        body = client.post("/agent/trigger?zone_id=z-lawn&delta_c=3.0").json()
        assert body["tag"] in ("REPLAN", "MONITOR", "NO-ACTION")
        assert body["toolTrace"], "a directive with no tool trace is unauditable"
        for call in body["toolTrace"]:
            assert call["tool"]
            json.loads(call["output"])

    def test_directive_numbers_are_grounded_in_tool_output(self, client):
        """The guardrail that matters. Every figure in a published directive must
        trace to something a tool returned."""
        body = client.post("/agent/trigger?zone_id=z-lawn&delta_c=3.0").json()
        assert body["grounded"] is True
        assert body["rejectedNumbers"] == []

    def test_directive_declares_which_engine_produced_it(self, client):
        """A deterministic fallback wearing the agent's clothes would be a lie
        about the primary track, and a model-written directive must name the
        exact model for the submission's AI-tools disclosure."""
        body = client.post("/agent/trigger?zone_id=z-lawn&delta_c=2.0").json()
        assert body["engine"], "every directive must say what produced it"
        # No key is configured for these tests, so it must be the labelled
        # fallback rather than anything that could be mistaken for a model.
        assert body["engine"] == "deterministic"
        assert body["promptVersion"]

    def test_health_reports_whether_a_model_is_configured(self, client):
        body = client.get("/health").json()
        assert body["model_configured"] is False
        assert body["model_provider"] is None


class TestCredits:
    def test_credit_ledger_is_exposed(self, client):
        """The brief requires credit spend logged per endpoint from day one."""
        body = client.get("/credits").json()
        assert {"live_calls_by_endpoint", "cache_hits_by_endpoint",
                "live_calls_total", "cache_hits_total"} <= set(body)
