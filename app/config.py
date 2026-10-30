"""Validate configuration from explicit values, the environment and local .env."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

# A token is configured by its SHA-256 digest, so the secret is never stored.
TokenDigest = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$")
]


class Settings(BaseSettings):
    """Reject invalid configuration before the HTTP service starts."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = (
        "ai-devops-agent"
    )
    app_env: Literal["development", "test", "production"] = "development"
    # Tracebacks are disabled unless a developer explicitly opts in locally.
    app_debug: bool = False
    # The investigation API stays closed until at least one digest is set.
    app_read_token_sha256: TokenDigest | None = None
    app_write_token_sha256: TokenDigest | None = None
    app_store_path: Path = Path("var/investigations.sqlite3")
    # The Claude agent provider is used only when a command asks for it; the
    # Anthropic SDK reads its own credentials from the environment.
    app_agent_model: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^claude-[a-z0-9-]+$")
    ] = "claude-opus-5-5"
    app_agent_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    app_agent_max_seconds: Annotated[int, Field(ge=10, le=1_800)] = 300
    app_agent_request_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 60.0

    @property
    def investigation_api_enabled(self) -> bool:
        """Report whether any access token is configured."""
        return bool(self.app_read_token_sha256 or self.app_write_token_sha256)


@lru_cache
def get_settings() -> Settings:
    """Read configuration once per process; restart after environment changes."""
    return Settings()
