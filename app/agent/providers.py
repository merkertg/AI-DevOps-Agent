"""Choose the agent provider and its limits from an explicit request."""

from dataclasses import replace
from typing import Literal

from app.agent.contracts import (
    DEFAULT_AGENT_LIMITS,
    AgentError,
    AgentLimits,
    AgentProvider,
)
from app.agent.offline import OfflineRulesProvider
from app.config import Settings

ProviderName = Literal["offline", "anthropic"]
PROVIDERS: tuple[ProviderName, ...] = ("offline", "anthropic")


def select_provider(
    name: ProviderName, settings: Settings
) -> tuple[AgentProvider, AgentLimits]:
    """Return the offline provider, or Claude with its own time budget."""
    if name == "offline":
        return OfflineRulesProvider(), DEFAULT_AGENT_LIMITS
    # The SDK is imported only when a run asks for the model provider.
    from app.agent.claude_provider import create_claude_provider, has_credentials

    provider = create_claude_provider(
        model=settings.app_agent_model,
        effort=settings.app_agent_effort,
        timeout_seconds=settings.app_agent_request_timeout_seconds,
    )
    if not has_credentials(provider):
        raise AgentError(
            "Anthropic credentials are not configured; set ANTHROPIC_API_KEY or "
            "sign in with the ant CLI."
        )
    return provider, replace(
        DEFAULT_AGENT_LIMITS, max_seconds=settings.app_agent_max_seconds
    )
