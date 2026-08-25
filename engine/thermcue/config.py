"""Runtime configuration.

Every secret arrives through the environment. Nothing in this module may carry a
default that is a credential; a missing key degrades the engine to cache-only
service rather than failing the request, because the judging deployment must
stay up even with the network removed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENGINE_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ENGINE_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
FIXTURE_DIR = DATA_DIR / "fixtures"
RESEARCH_DIR = ENGINE_ROOT.parent / "research"


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

    # --- Anthropic (the agent) -------------------------------------------
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    agent_model: str = Field(default="claude-sonnet-4-5-20250929", alias="THERMCUE_AGENT_MODEL")
    agent_temperature: float = Field(default=0.0, alias="THERMCUE_AGENT_TEMPERATURE")
    agent_max_tokens: int = Field(default=2048, alias="THERMCUE_AGENT_MAX_TOKENS")
    agent_tick_seconds: float = Field(default=120.0, alias="THERMCUE_AGENT_TICK_SECONDS")

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

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
