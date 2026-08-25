"""Model-provider tests for the agent.

The hackathon supplies FortyGuard credits and no model credits, so the model is
bring-your-own and the engine supports any OpenAI-compatible endpoint alongside
Anthropic. That path has to be proven without a key, or it is untested code
sitting on the primary track.

These tests stand up a fake OpenAI-compatible provider with respx and drive the
real agent through it: a tool-calling round, then a directive. They assert the
protocol translation, the three defensive cases free tiers actually hit, and -
most importantly - that a model which invents a number still gets its directive
rejected.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from thermcue.agent import (
    MAX_TOOL_ROUNDS,
    AgentTools,
    Directive,
    ThermCueAgent,
    collect_tool_numbers,
    ground_numbers,
)
from thermcue.config import LLM_PRESETS, Settings

BASE = "https://fake-provider.test/v1"


def bare(**values) -> Settings:
    """Settings built from arguments only.

    ``_env_file=None`` matters: without it pydantic-settings reads the
    developer's engine/.env, so once a real Groq key is configured locally these
    tests start asserting against that key instead of the fixture. A test whose
    result depends on who ran it is not a test.
    """
    return Settings(_env_file=None, **values)


def make_settings(**overrides) -> Settings:
    """Settings pointed at the fake provider, with no real key anywhere."""
    values = {
        "THERMCUE_LLM_API_KEY": "test-key-not-real",
        "THERMCUE_LLM_PROVIDER": "openai",
        "THERMCUE_LLM_BASE_URL": BASE,
        "THERMCUE_AGENT_MODEL": "fake-model",
        "THERMCUE_OFFLINE": "1",
        "ANTHROPIC_API_KEY": "",
        **overrides,
    }
    return bare(**{k: v for k, v in values.items()})


def tool_call_response(name: str, arguments: str = "{}") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            }
        ]
    }


def text_response(text) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class TestPresets:
    def test_every_preset_declares_a_protocol_we_implement(self):
        for name, preset in LLM_PRESETS.items():
            assert preset.protocol in ("anthropic", "openai"), name
            assert preset.base_url.startswith("https://"), name
            assert preset.default_model, name
            assert preset.label, name

    def test_free_tier_providers_are_all_openai_compatible(self):
        """One protocol implementation has to cover every free tier, or the
        bring-your-own-key story only works for people who already pay."""
        for name in ("qwen", "groq", "openrouter", "cerebras", "deepseek", "together"):
            assert LLM_PRESETS[name].protocol == "openai"

    def test_provider_is_inferred_from_whichever_key_is_set(self):
        assert bare(ANTHROPIC_API_KEY="x").resolved_provider == "anthropic"
        assert bare(THERMCUE_LLM_API_KEY="x").resolved_provider == "openai"
        assert bare().resolved_provider is None

    def test_explicit_provider_beats_inference(self):
        s = bare(THERMCUE_LLM_API_KEY="x", THERMCUE_LLM_PROVIDER="groq")
        assert s.llm.provider == "groq"
        assert "groq.com" in s.llm.base_url

    def test_preset_supplies_the_default_model(self):
        s = bare(THERMCUE_LLM_API_KEY="x", THERMCUE_LLM_PROVIDER="qwen")
        assert s.llm.model == LLM_PRESETS["qwen"].default_model

    def test_explicit_model_beats_the_preset(self):
        s = bare(
            THERMCUE_LLM_API_KEY="x",
            THERMCUE_LLM_PROVIDER="qwen",
            THERMCUE_AGENT_MODEL="qwen-max",
        )
        assert s.llm.model == "qwen-max"

    def test_unknown_provider_is_an_error_not_a_silent_fallback(self):
        s = bare(THERMCUE_LLM_API_KEY="x", THERMCUE_LLM_PROVIDER="not-a-provider")
        with pytest.raises(ValueError, match="Unknown THERMCUE_LLM_PROVIDER"):
            _ = s.llm
        # has_model must not raise; the engine has to keep serving.
        assert s.has_model is False

    def test_no_key_means_no_model_rather_than_an_exception(self):
        """The engine must serve the whole application with no model key at all,
        running the deterministic path and labelling it."""
        s = bare()
        assert s.llm is None
        assert s.has_model is False


class TestOpenAiProtocol:
    @respx.mock
    async def test_agent_completes_a_tool_round_then_publishes(self, scenario):
        settings = make_settings()
        route = respx.post(f"{BASE}/chat/completions")
        route.side_effect = [
            httpx.Response(200, json=tool_call_response("get_thermal_state")),
            httpx.Response(200, json=text_response("assessment complete")),
            httpx.Response(
                200,
                json=text_response("NO-ACTION | Conditions hold. | Plan stands."),
            ),
        ]
        agent = ThermCueAgent(scenario, settings)
        directive = await agent.decide()

        assert directive.tag == "NO-ACTION"
        assert directive.grounded is True
        assert [c.name for c in directive.tool_calls] == ["get_thermal_state"]
        # Names the model that ran, not the preset's default. The preset label
        # is only right when the model was not overridden, and this string is
        # the submission's AI-tools disclosure, so it has to be true.
        assert directive.engine == "fake-model (openai)"

    @respx.mock
    async def test_tools_are_sent_in_openai_function_shape(self, scenario):
        settings = make_settings()
        captured: list[dict] = []

        def record(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200, json=text_response("NO-ACTION | Nothing moved. | Plan stands.")
            )

        respx.post(f"{BASE}/chat/completions").mock(side_effect=record)
        await ThermCieAgentShim(scenario, settings).decide()

        first = captured[0]
        assert first["model"] == "fake-model"
        assert first["tool_choice"] == "auto"
        for tool in first["tools"]:
            # Anthropic uses input_schema; OpenAI nests parameters under
            # function. Getting this wrong means the model sees no tools and
            # invents everything.
            assert tool["type"] == "function"
            assert {"name", "description", "parameters"} <= set(tool["function"])
        assert first["messages"][0]["role"] == "system"

    @respx.mock
    async def test_bearer_token_is_sent(self, scenario):
        settings = make_settings()
        seen: list[str] = []

        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization", ""))
            return httpx.Response(
                200, json=text_response("NO-ACTION | Steady. | Plan stands.")
            )

        respx.post(f"{BASE}/chat/completions").mock(side_effect=record)
        await ThermCieAgentShim(scenario, settings).decide()
        assert seen and seen[0] == "Bearer test-key-not-real"

    @respx.mock
    async def test_provider_error_surfaces_rather_than_being_swallowed(self, scenario):
        """A 500 is not retryable and must reach the operator.

        Deliberately not a 429: that is retried now, and asserting it here made
        this single test sleep through the whole backoff schedule for 65 seconds.
        Rate limiting has its own tests, with the backoff neutralised.
        """
        settings = make_settings()
        respx.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(500, text="upstream exploded")
        )
        directive = await ThermCieAgentShim(scenario, settings).decide()
        assert directive.grounded is False
        assert "500" in directive.text or "exploded" in directive.text


class TestFreeTierQuirks:
    """The three things free tiers actually do that paid ones do not."""

    @respx.mock
    async def test_malformed_tool_arguments_do_not_kill_the_run(self, scenario):
        settings = make_settings()
        respx.post(f"{BASE}/chat/completions").mock(
            side_effect=[
                # Not valid JSON. Seen from free tiers on no-argument tools.
                httpx.Response(
                    200, json=tool_call_response("get_thermal_state", arguments="")
                ),
                httpx.Response(200, json=text_response("done")),
                httpx.Response(
                    200, json=text_response("NO-ACTION | Holding. | Plan stands.")
                ),
            ]
        )
        directive = await ThermCieAgentShim(scenario, settings).decide()
        assert directive.tag == "NO-ACTION"
        assert [c.name for c in directive.tool_calls] == ["get_thermal_state"]

    @respx.mock
    async def test_content_returned_as_parts_is_joined(self, scenario):
        settings = make_settings()
        respx.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=text_response(
                    [{"type": "text", "text": "NO-ACTION | Stable. | Plan stands."}]
                ),
            )
        )
        directive = await ThermCieAgentShim(scenario, settings).decide()
        assert directive.tag == "NO-ACTION"

    @respx.mock
    async def test_reasoning_block_is_stripped(self, scenario):
        settings = make_settings()
        respx.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=text_response(
                    "<think>The bands look steady, nothing crosses.</think>\n"
                    "NO-ACTION | Nothing crosses a band. | Plan stands."
                ),
            )
        )
        directive = await ThermCieAgentShim(scenario, settings).decide()
        assert directive.tag == "NO-ACTION"
        assert "<think>" not in directive.text

    @respx.mock
    async def test_tool_loop_is_bounded(self, scenario):
        """A model that calls tools forever is stuck, not thorough."""
        settings = make_settings()
        respx.post(f"{BASE}/chat/completions").mock(
            side_effect=[
                httpx.Response(200, json=tool_call_response("get_thermal_state"))
                for _ in range(MAX_TOOL_ROUNDS)
            ]
            + [httpx.Response(200, json=text_response("NO-ACTION | Bounded. | Stands."))]
        )
        directive = await ThermCieAgentShim(scenario, settings).decide()
        assert len(directive.tool_calls) <= MAX_TOOL_ROUNDS


class TestGroundingAppliesToEveryProvider:
    @respx.mock
    async def test_an_invented_number_is_rejected(self, scenario):
        """The guardrail is protocol-independent. A cheap model that hallucinates
        a figure must be caught exactly as an expensive one would be."""
        settings = make_settings()
        respx.post(f"{BASE}/chat/completions").mock(
            side_effect=[
                httpx.Response(200, json=tool_call_response("get_thermal_state")),
                httpx.Response(200, json=text_response("done")),
                httpx.Response(
                    200,
                    json=text_response(
                        "REPLAN | Event Lawn hits WBGT 88.4 at 16:00. Open Gate C. "
                        "| Exposure falls 73.9%."
                    ),
                ),
            ]
        )
        directive = await ThermCieAgentShim(scenario, settings).decide()
        assert directive.grounded is False
        assert "Directive withheld" in directive.text

    def test_grounding_helpers_are_provider_agnostic(self):
        allowed = {30.1, 21.4}
        assert ground_numbers("REPLAN | 30.1 est | falls 21.4%.", allowed) == []
        assert ground_numbers("REPLAN | 55.2 est.", allowed) == [55.2]

    def test_clock_times_are_not_treated_as_claims(self):
        assert ground_numbers("Open Gate C at 13:40.", set()) == []


class ThermCieAgentShim(ThermCueAgent):
    """Agent with the reference bands pre-seeded.

    A cold agent publishes MONITOR on its first cycle to establish a baseline and
    never reaches the model, which would make every test above assert nothing.
    """

    async def decide(self, perturbation=None) -> Directive:
        self.reference_bands = {"z-plaza": {15: "moderate"}}
        return await super().decide(perturbation=perturbation)


class TestEngineLabel:
    """The label is the submission's AI-tools disclosure. It has to be true."""

    def test_label_names_the_model_that_actually_ran(self):
        agent = ThermCueAgent(
            None,
            bare(
                THERMCUE_LLM_API_KEY="x",
                THERMCUE_LLM_PROVIDER="groq",
                THERMCUE_AGENT_MODEL="openai/gpt-oss-120b",
            ),
        )
        assert agent._engine_label() == "openai/gpt-oss-120b (groq)"

    def test_unoverridden_model_still_names_the_exact_model_id(self):
        """The preset's friendly label is never published. A directive from
        gpt-oss-120b must not read "Llama 3.3 70B" just because the preset
        default was not overridden."""
        agent = ThermCueAgent(
            None, bare(THERMCUE_LLM_API_KEY="x", THERMCUE_LLM_PROVIDER="groq")
        )
        assert agent._engine_label() == "openai/gpt-oss-120b (groq)"

    def test_no_model_is_labelled_deterministic(self):
        assert ThermCueAgent(None, bare())._engine_label() == "deterministic"

    def test_misconfiguration_is_labelled_not_silently_deterministic(self):
        agent = ThermCueAgent(
            None, bare(THERMCUE_LLM_API_KEY="x", THERMCUE_LLM_PROVIDER="nope")
        )
        assert agent._engine_label() == "misconfigured"


class TestToolDispatchRobustness:
    """Models invent arguments and repeat calls. Neither may cost a cycle."""

    async def test_invented_arguments_are_dropped_not_crashed_on(self, scenario):
        """A live free-tier model called get_thermal_state(hours=...) for a tool
        that takes none. Passing that through raised TypeError and killed the
        whole cycle."""
        tools = AgentTools(scenario, make_settings())
        result = await tools.dispatch(
            "get_thermal_state", {"hours": [15, 16], "zone": "z-lawn"}
        )
        assert result["_ignored_arguments"] == ["hours", "zone"]
        assert result["zones"], "the tool must still have run"

    async def test_unknown_tool_reports_the_real_ones(self, scenario):
        tools = AgentTools(scenario, make_settings())
        result = await tools.dispatch("summon_rain", {})
        assert "unknown tool" in result["error"]
        assert "get_thermal_state" in result["available_tools"]

    async def test_a_repeated_call_returns_a_pointer_not_the_payload(self, scenario):
        """A live run called the same tool three times and spent the whole
        tokens-per-minute budget re-reading one answer."""
        tools = AgentTools(scenario, make_settings())
        first = await tools.dispatch("get_thermal_state", {})
        assert first["zones"]

        second = await tools.dispatch("get_thermal_state", {})
        assert second["_already_returned"] is True
        assert "zones" not in second
        assert len(json.dumps(second)) < len(json.dumps(first)) / 4

    async def test_different_arguments_are_not_deduplicated(self, scenario):
        tools = AgentTools(scenario, make_settings())
        a = await tools.dispatch("run_simulation", {"plan_label": "baseline"})
        b = await tools.dispatch("run_simulation", {"plan_label": "optimised"})
        assert "_already_returned" not in a
        assert "_already_returned" not in b


class TestModelView:
    """What the model sees is not what the audit trace records."""

    async def test_thermal_state_is_projected_down_for_the_model(self, scenario):
        from thermcue.agent import model_view

        tools = AgentTools(scenario, make_settings())
        full = await tools.get_thermal_state()
        compact = model_view("get_thermal_state", full)

        assert len(json.dumps(compact)) < len(json.dumps(full)) / 3
        # Dropped, never altered: everything the model can quote is still a
        # figure a tool returned, so grounding is unaffected.
        assert "escalations_within_horizon" in compact
        assert "hottest_hour_per_zone" in compact

    async def test_the_audit_trace_keeps_the_full_payload(self, scenario):
        """The trace must be complete or it is not evidence."""
        tools = AgentTools(scenario, make_settings())
        await tools.get_thermal_state()
        assert len(tools.calls[0].result["zones"]) == 35
        json.loads(tools.calls[0].to_trace().output)


@pytest.fixture()
def no_backoff(monkeypatch):
    """Neutralise the retry backoff so the tests assert behaviour, not patience."""

    async def instant(_seconds: float) -> None:
        return None

    import thermcue.agent as agent_module

    monkeypatch.setattr(agent_module, "_backoff_sleep", instant)


class TestRateLimitHandling:
    """A metered free tier refuses mid-cycle. That must cost time, not a directive."""

    def test_retry_after_header_is_honoured(self):
        from thermcue.agent import _retry_after_seconds

        response = httpx.Response(429, headers={"retry-after": "7"})
        assert _retry_after_seconds(response, fallback=99.0) == 7.0

    def test_retry_delay_is_read_from_the_error_body(self):
        """Groq puts the figure only in the body, and the deployed agent failed
        in four seconds while the body said to try again in 3.4."""
        from thermcue.agent import _retry_after_seconds

        response = httpx.Response(
            429,
            json={
                "error": {
                    "message": "Rate limit reached ... Please try again in 3.435s. Need more tokens?",
                    "code": "rate_limit_exceeded",
                }
            },
        )
        assert _retry_after_seconds(response, fallback=99.0) == pytest.approx(4.435)

    def test_falls_back_to_our_own_backoff_when_unsaid(self):
        from thermcue.agent import _retry_after_seconds

        assert _retry_after_seconds(httpx.Response(429, text="nope"), fallback=8.0) == 8.0

    @respx.mock
    async def test_a_rate_limit_is_retried_not_fatal(self, scenario, no_backoff):
        settings = make_settings()
        route = respx.post(f"{BASE}/chat/completions").mock(
            side_effect=[
                # The tool-loop call is refused twice, then succeeds.
                httpx.Response(429, json={"error": {"message": "try again in 0.01s"}}),
                httpx.Response(429, json={"error": {"message": "try again in 0.01s"}}),
                httpx.Response(200, json=text_response("assessment complete")),
                # The agent then asks for the directive; that call is refused once.
                httpx.Response(429, json={"error": {"message": "try again in 0.01s"}}),
                httpx.Response(
                    200, json=text_response("NO-ACTION | Recovered. | Plan stands.")
                ),
            ]
        )
        directive = await ThermCieAgentShim(scenario, settings).decide()
        assert directive.tag == "NO-ACTION"
        assert directive.grounded is True
        assert route.call_count == 5, "every refusal must have been retried"

    @respx.mock
    async def test_a_persistent_rate_limit_eventually_surfaces(self, scenario, no_backoff):
        """Retrying forever would hang the agent loop. It gives up and says so."""
        settings = make_settings()
        respx.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": {"message": "try again in 0.01s"}})
        )
        directive = await ThermCieAgentShim(scenario, settings).decide()
        assert directive.grounded is False
        assert "429" in directive.text or "rate limited" in directive.text

    @respx.mock
    async def test_a_non_429_error_is_not_retried(self, scenario):
        """Retrying a 401 wastes the operator's time on a key that will never work."""
        settings = make_settings()
        route = respx.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(401, text="invalid api key")
        )
        directive = await ThermCieAgentShim(scenario, settings).decide()
        assert directive.grounded is False
        assert route.call_count == 1
