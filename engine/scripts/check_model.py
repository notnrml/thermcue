#!/usr/bin/env python
"""Validate a model provider key and run one real agent cycle against it.

The hackathon supplies no model credits, so the agent's model is bring-your-own
and several providers have a free tier. This script is the thirty-second check
that a key actually works before you set it on the deployment and hope.

It does four things in order, and stops at the first failure with a specific
reason rather than a stack trace:

1. Resolves the provider, base URL and model from the environment.
2. Makes one minimal chat completion, proving the key and the endpoint.
3. Makes one tool-calling round, proving the model can actually use tools -
   which is the part free tiers most often get wrong, and without which the
   agent is just an expensive way to produce prose.
4. Runs one full agent decision cycle and reports whether the directive was
   grounded.

    THERMCUE_LLM_PROVIDER=groq THERMCUE_LLM_API_KEY=gsk_... \\
      .venv/bin/python scripts/check_model.py

Add --live to let step 4 hit FortyGuard; by default it runs offline against the
committed cache so the check costs no FortyGuard credits.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from thermcue.agent import ThermCueAgent  # noqa: E402
from thermcue.config import LLM_PRESETS, get_settings  # noqa: E402
from thermcue.scenario import load_scenario  # noqa: E402

OK = "  ok   "
BAD = " FAIL  "


def main() -> int:
    if "--live" not in sys.argv:
        os.environ.setdefault("THERMCUE_OFFLINE", "1")
    get_settings.cache_clear()
    settings = get_settings()

    print("1. Resolving provider")
    try:
        llm = settings.llm
    except ValueError as exc:
        print(f"{BAD} {exc}")
        return 2
    if llm is None:
        print(f"{BAD} No model key found.")
        print()
        print("     Set two variables. Providers with a free tier:")
        for name in ("groq", "cerebras", "openrouter", "qwen"):
            preset = LLM_PRESETS[name]
            print(f"       THERMCUE_LLM_PROVIDER={name:<12} -> {preset.label}")
        print()
        print("       THERMCUE_LLM_API_KEY=<your key>")
        return 2
    print(f"{OK} {llm.label}")
    print(f"       protocol {llm.protocol} | model {llm.model}")
    print(f"       {llm.base_url}")

    if llm.protocol == "anthropic":
        print("\n2-3. Skipped: the Anthropic path is exercised by its own SDK.")
    else:
        print("\n2. Minimal completion")
        try:
            body = _post(
                llm,
                {
                    "model": llm.model,
                    "messages": [{"role": "user", "content": "Reply with the word ready."}],
                    "max_tokens": 16,
                },
            )
            content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            print(f"{OK} responded: {str(content)[:60]!r}")
        except Exception as exc:  # noqa: BLE001 - the whole point is to report it
            print(f"{BAD} {exc}")
            return 3

        print("\n3. Tool calling")
        try:
            body = _post(
                llm,
                {
                    "model": llm.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Use the get_band tool for zone z-lawn at hour 16.",
                        }
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "get_band",
                                "description": "Heat band for a zone at an hour.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "zone_id": {"type": "string"},
                                        "hour": {"type": "integer"},
                                    },
                                    "required": ["zone_id", "hour"],
                                },
                            },
                        }
                    ],
                    "tool_choice": "auto",
                    "max_tokens": 128,
                },
            )
            calls = (
                ((body.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
            )
            if not calls:
                print(f"{BAD} model did not call the tool.")
                print("       This provider or model cannot drive the agent. Try another")
                print("       model on the same key before switching provider.")
                return 4
            fn = calls[0].get("function") or {}
            print(f"{OK} called {fn.get('name')} with {fn.get('arguments')}")
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD} {exc}")
            return 4

    print("\n4. One full agent cycle")
    scenario = load_scenario()
    agent = ThermCueAgent(scenario, settings)

    async def run():
        await agent.decide()  # cold start seeds the reference bands
        return await agent.decide(perturbation={"z-lawn": 3.0})

    directive = asyncio.run(run())
    status = OK if directive.grounded else BAD
    print(f"{status} {directive.tag} via {directive.engine}")
    print(f"       tools: {[c.name for c in directive.tool_calls]}")
    print(f"       {directive.text[:240]}")
    if not directive.grounded:
        print(f"       ungrounded figures: {directive.rejected_numbers}")
        print("       The directive was withheld, which is the guardrail working.")
        print("       A model that keeps doing this is not usable for the agent.")
        return 5

    print("\nReady. Set the same two variables on the deployment:")
    print(f"  flyctl secrets set THERMCUE_LLM_PROVIDER={llm.provider} \\")
    print("    THERMCUE_LLM_API_KEY=<your key> --app thermcue-engine")
    return 0


def _post(llm, payload: dict) -> dict:
    response = httpx.post(
        f"{llm.base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers={
            "Authorization": f"Bearer {llm.api_key}",
            "Content-Type": "application/json",
        },
        timeout=60.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


if __name__ == "__main__":
    raise SystemExit(main())
