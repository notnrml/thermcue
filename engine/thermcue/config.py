"""Runtime configuration.

Every secret arrives through the environment. Nothing in this module may carry a
default that is a credential; a missing key degrades the engine to cache-only
service rather than failing the request, because the judging deployment must
stay up even with the network removed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dataclasses import dataclass

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENGINE_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ENGINE_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
FIXTURE_DIR = DATA_DIR / "fixtures"
RESEARCH_DIR = ENGINE_ROOT.parent / "research"


@dataclass(slots=True, frozen=True)
class LlmPreset:
    """A model provider the agent knows how to talk to out of the box."""

    protocol: str
    """``anthropic`` or ``openai``. Determines the request shape, not the vendor."""
    base_url: str
    default_model: str
    label: str
    """Human-readable name, published in every directive so a reader can see
    which model produced it."""


#: Providers with a usable free tier come first. Every one of these except
#: Anthropic speaks the OpenAI chat-completions protocol, so supporting that one
#: protocol covers all of them.
LLM_PRESETS: dict[str, LlmPreset] = {
    "qwen": LlmPreset(
        protocol="openai",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        label="Qwen (Alibaba DashScope)",
    ),
    "groq": LlmPreset(
        protocol="openai",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        label="Llama 3.3 70B (Groq)",
    ),
    "openrouter": LlmPreset(
        protocol="openai",
        base_url="https://openrouter.ai/api/v1",
        default_model="qwen/qwen-2.5-72b-instruct",
        label="Qwen 2.5 72B (OpenRouter)",
    ),
    "cerebras": LlmPreset(
        protocol="openai",
        base_url="https://api.cerebras.ai/v1",
        default_model="llama-3.3-70b",
        label="Llama 3.3 70B (Cerebras)",
    ),
    "deepseek": LlmPreset(
        protocol="openai",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        label="DeepSeek Chat",
    ),
    "together": LlmPreset(
        protocol="openai",
        base_url="https://api.together.xyz/v1",
        default_model="Qwen/Qwen2.5-72B-Instruct-Turbo",
        label="Qwen 2.5 72B (Together)",
    ),
    "openai": LlmPreset(
        protocol="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        label="OpenAI",
    ),
    "anthropic": LlmPreset(
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-5-20250929",
        label="Claude Sonnet 4.5 (Anthropic)",
    ),
}


@dataclass(slots=True, frozen=True)
class LlmConfig:
    """Resolved model configuration for one agent run."""

    provider: str
    protocol: str
    base_url: str
    model: str
    api_key: str
    label: str


class Settings(BaseSettings):
    """Engine settings, all overridable by environment variable."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- FortyGuard -------------------------------------------------------
    fortyguard_api_key: str | None = Field(default=None, alias="FORTYGUARD_API_KEY")
    fortyguard_base_url: str = Field(
        default="https://api.fortyguard.com", alias="FORTYGUARD_BASE_URL"
    )
    fortyguard_timeout_s: float = Field(default=60.0, alias="FORTYGUARD_TIMEOUT_S")
    fortyguard_poll_interval_s: float = Field(default=3.0, alias="FORTYGUARD_POLL_INTERVAL_S")
    fortyguard_task_timeout_s: float = Field(default=600.0, alias="FORTYGUARD_TASK_TIMEOUT_S")
    fortyguard_max_retries: int = Field(default=4, alias="FORTYGUARD_MAX_RETRIES")

    # --- The agent's language model --------------------------------------
    #
    # The hackathon supplies a FortyGuard Temperature API key and credits. It
    # supplies no model credits: the participant benefits list is API access,
    # trial credits, quickstart, docs, Slack, support, certificate and partner
    # network, and all five Platform & API FAQ entries are about the Temperature
    # API. The submission form asks you to *disclose* which AI tools you used,
    # which only makes sense if you brought them. So the model is bring-your-own,
    # and this engine is deliberately not tied to one vendor.
    #
    # Two protocols are supported: Anthropic's Messages API, and any
    # OpenAI-compatible /chat/completions endpoint with tool calling. The second
    # covers every free tier worth having - Qwen via DashScope, Groq, OpenRouter,
    # Cerebras, DeepSeek, Together - so a free key is one environment variable.
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_api_key: str | None = Field(default=None, alias="THERMCUE_LLM_API_KEY")
    llm_provider: str | None = Field(default=None, alias="THERMCUE_LLM_PROVIDER")
    """Named preset from ``LLM_PRESETS``, or ``None`` to infer from whichever key
    is set. An explicit provider with no matching key is a configuration error
    and is reported as one rather than silently falling back."""
    llm_base_url: str | None = Field(default=None, alias="THERMCUE_LLM_BASE_URL")
    """Overrides the preset's base URL. Set this for a provider not listed."""
    agent_model: str = Field(default="", alias="THERMCUE_AGENT_MODEL")
    """Empty means take the preset's default model."""
    agent_temperature: float = Field(default=0.0, alias="THERMCUE_AGENT_TEMPERATURE")
    agent_max_tokens: int = Field(default=2048, alias="THERMCUE_AGENT_MAX_TOKENS")
    agent_tick_seconds: float = Field(default=120.0, alias="THERMCUE_AGENT_TICK_SECONDS")
    agent_request_timeout_s: float = Field(
        default=90.0, alias="THERMCUE_AGENT_REQUEST_TIMEOUT_S"
    )
    """Per-request timeout for the model call. Free tiers queue, so this is
    generous - but it is explicit, because a model call with no timeout is a
    hang, and a hang in the agent loop is an outage."""

    # --- Behaviour --------------------------------------------------------
    offline: bool = Field(default=False, alias="THERMCUE_OFFLINE")
    """Hard offline mode. Serves cache only; never opens a socket. Used to prove
    the judging fallback works with networking disabled."""

    cache_dir: Path = Field(default=CACHE_DIR, alias="THERMCUE_CACHE_DIR")
    scenario_path: Path = Field(
        default=DATA_DIR / "scenario_phoenix.json", alias="THERMCUE_SCENARIO_PATH"
    )
    drivers_path: Path = Field(
        default=RESEARCH_DIR / "zone_heat_drivers.json", alias="THERMCUE_DRIVERS_PATH"
    )
    cors_origins: str = Field(default="*", alias="THERMCUE_CORS_ORIGINS")

    @property
    def has_fortyguard_key(self) -> bool:
        return bool(self.fortyguard_api_key)

    # ------------------------------------------------------------ the agent

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def resolved_provider(self) -> str | None:
        """Which preset to use, or None when no model key is configured.

        Explicit choice wins. Otherwise infer: an Anthropic key means Anthropic,
        a generic key means the OpenAI-compatible path.
        """
        if self.llm_provider:
            return self.llm_provider.strip().lower()
        if self.anthropic_api_key:
            return "anthropic"
        if self.llm_api_key:
            return "openai"
        return None

    @property
    def llm(self) -> "LlmConfig | None":
        """Fully resolved model configuration, or None if the agent has no model.

        Returning None rather than raising is deliberate: the engine must serve
        the whole application without a model key, running the agent's
        deterministic path and labelling it as such.
        """
        provider = self.resolved_provider
        if provider is None:
            return None
        preset = LLM_PRESETS.get(provider)
        if preset is None:
            raise ValueError(
                f"Unknown THERMCUE_LLM_PROVIDER {provider!r}. "
                f"Known presets: {', '.join(sorted(LLM_PRESETS))}. "
                f"For anything else, set THERMCUE_LLM_PROVIDER=openai with "
                f"THERMCUE_LLM_BASE_URL."
            )
        key = self.anthropic_api_key if preset.protocol == "anthropic" else self.llm_api_key
        if not key:
            return None
        return LlmConfig(
            provider=provider,
            protocol=preset.protocol,
            base_url=self.llm_base_url or preset.base_url,
            model=self.agent_model or preset.default_model,
            api_key=key,
            label=preset.label,
        )

    @property
    def has_model(self) -> bool:
        try:
            return self.llm is not None
        except ValueError:
            return False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
