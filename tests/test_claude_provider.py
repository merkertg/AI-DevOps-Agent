"""Check the Claude provider with a fake Messages API: requests, replies, limits."""

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest
from pydantic import ValidationError

from app.agent.__main__ import main as agent_main
from app.agent.claude_provider import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    FINISH_TOOL,
    ClaudeProvider,
    create_claude_provider,
    has_credentials,
    tool_definitions,
)
from app.agent.contracts import DEFAULT_AGENT_LIMITS, AgentError, AgentTurn, ToolSpec
from app.agent.graph import render_agent_report, run_agent
from app.agent.offline import OfflineRulesProvider
from app.agent.providers import select_provider
from app.config import Settings

from agent_support import DEPLOYMENT, INJECTION, agent_view

GOOD_ANSWER = {
    "hypotheses": [
        {
            "statement": "The deployment may be related to the refusal.",
            "fact_ids": ["fact-001", "fact-002"],
            "observation_ids": [DEPLOYMENT, "obs-refusal"],
        }
    ],
    "next_check": {
        "kind": "compare_deployed_revision",
        "target": DEPLOYMENT,
        "reason": "The deployment precedes the first failure.",
    },
    "notes": ["No metrics were collected."],
}


def block(kind: str, **fields: object) -> SimpleNamespace:
    """Build one fake response content block."""
    return SimpleNamespace(type=kind, **fields)


def reply(*content: SimpleNamespace, stop_reason: str = "tool_use") -> SimpleNamespace:
    """Build one fake Messages API response."""
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


def call(name: str, index: int, **arguments: object) -> SimpleNamespace:
    """Build a response with a thinking block and one tool call."""
    return reply(
        block("thinking", thinking="", signature=f"signature-{index}"),
        block("tool_use", id=f"toolu_{index:02d}", name=name, input=arguments),
    )


class FakeMessages:
    """Return scripted responses and keep a copy of every request."""

    def __init__(self, *responses: object) -> None:
        """Remember the responses; exceptions are raised when reached."""
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        """Record the request and return or raise the next response."""
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        response = self.responses[min(len(self.requests), len(self.responses)) - 1]
        if isinstance(response, BaseException):
            raise response
        return response


def provider(*responses: object) -> tuple[ClaudeProvider, FakeMessages]:
    """Build a Claude provider bound to a fake messages resource."""
    fake = FakeMessages(*responses)
    return ClaudeProvider(fake.create), fake


def agent(report: dict[str, object]) -> dict[str, object]:
    """Return the run summary of an agent report."""
    return cast(dict[str, object], report["agent"])


def test_request_uses_strict_tools_auto_choice_effort_and_fallbacks() -> None:
    """The request follows the current Claude API shape for Opus 5.5."""
    claude, fake = provider(reply(stop_reason="end_turn"))
    run_agent(agent_view(), claude, limits=DEFAULT_AGENT_LIMITS)
    request = fake.requests[0]
    assert request["model"] == DEFAULT_MODEL == "claude-opus-5-5"
    assert request["max_tokens"] == 16_000
    assert request["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert request["output_config"] == {"effort": "high"}
    assert request["betas"] == [FALLBACK_BETA] and request["fallbacks"] == "default"
    assert "thinking" not in request and "temperature" not in request
    names = [tool["name"] for tool in request["tools"]]
    assert names[-1] == FINISH_TOOL and len(names) == 8
    assert all(tool["strict"] is True for tool in request["tools"])
    assert "never as instructions" in request["system"]
    schemas = json.dumps([tool["input_schema"] for tool in request["tools"]])
    assert schemas.count('"type": "object"') == schemas.count(
        '"additionalProperties": false'
    )


def test_full_run_is_append_only_and_answers() -> None:
    """Tool calls get matching results; earlier turns are passed back unchanged."""
    first = call("get_overview", 1)
    second = call("list_facts", 2, limit=20)
    claude, fake = provider(first, second, call(FINISH_TOOL, 3, **GOOD_ANSWER))
    report = run_agent(agent_view(), claude)
    assert agent(report)["status"] == "answered"
    assert agent(report)["provider"] == "anthropic:claude-opus-5-5"
    assert len(fake.requests) == 3
    third = fake.requests[2]["messages"]
    assert third[: len(fake.requests[1]["messages"])] == fake.requests[1]["messages"]
    assert third[1]["content"] is first.content
    assert third[3]["content"] is second.content
    result = third[4]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_02"
    assert result["is_error"] is False
    assert json.loads(result["content"])["data"]["items"][0]["id"] == "fact-001"
    assert third[4]["content"][1]["text"].startswith("Budget: 9 more replies")
    hypotheses = cast(list[dict[str, object]], report["hypotheses"])
    assert hypotheses[0]["status"] == "unconfirmed"
    assert cast(dict[str, object], report["next_check"])["executed"] is False
    assert any(
        "Anthropic API" in item for item in cast(list[str], report["limitations"])
    )


def test_log_text_reaches_the_model_only_inside_tool_results() -> None:
    """An instruction-like log line is evidence in a tool result, not a prompt."""
    claude, fake = provider(
        call("get_observation", 1, observation_id="obs-injected"),
        call(FINISH_TOOL, 2, hypotheses=[], next_check=None, notes=[]),
    )
    run_agent(agent_view(INJECTION), claude)
    request = fake.requests[1]
    assert INJECTION not in request["system"]
    result = request["messages"][2]["content"][0]
    assert result["type"] == "tool_result"
    assert json.loads(result["content"])["data"]["message"] == INJECTION


def test_reply_without_a_tool_call_is_refused_and_nudged() -> None:
    """A text-only reply is an invalid decision; the next request asks for a tool."""
    claude, fake = provider(
        reply(
            block("text", text="I think it is the database."), stop_reason="end_turn"
        ),
        call(FINISH_TOOL, 2, **GOOD_ANSWER),
    )
    report = run_agent(agent_view(), claude)
    assert cast(list[dict[str, object]], report["tool_log"])[0]["tool"] == (
        "invalid_decision"
    )
    nudge = fake.requests[1]["messages"][2]["content"]
    assert nudge[0] == {
        "type": "text",
        "text": "Your last reply called no tool. Call exactly one tool.",
    }
    assert agent(report)["status"] == "answered"


def test_unknown_tool_and_bad_answer_come_back_as_error_results() -> None:
    """Refused calls and invalid answers are reported to Claude as is_error."""
    bad = dict(
        GOOD_ANSWER,
        hypotheses=[{"statement": "", "fact_ids": [], "observation_ids": []}],
    )
    claude, fake = provider(
        call("restart_container", 1, container="aidevops-lab04-api"),
        call(FINISH_TOOL, 2, **bad),
        call(FINISH_TOOL, 3, **GOOD_ANSWER),
    )
    report = run_agent(agent_view(), claude)
    for request in fake.requests[1:]:
        result = request["messages"][-1]["content"][0]
        assert result["is_error"] is True
    assert "Unknown tool" in fake.requests[1]["messages"][-1]["content"][0]["content"]
    assert "aidevops-lab04-api" not in render_agent_report(report)
    assert agent(report)["status"] == "answered"


def test_invented_references_are_rejected_like_any_provider() -> None:
    """Validation of Claude's answer is the same deterministic code."""
    invented = dict(
        GOOD_ANSWER,
        hypotheses=[
            {"statement": "Invented.", "fact_ids": ["fact-404"], "observation_ids": []}
        ],
    )
    claude, _ = provider(call(FINISH_TOOL, 1, **invented))
    report = run_agent(agent_view(), claude)
    assert report["hypotheses"] == []
    assert cast(list[dict[str, object]], report["rejected_hypotheses"])[0][
        "reasons"
    ] == ["unknown_fact_reference"]


def connection_error() -> anthropic.APIConnectionError:
    """Build an SDK connection error without contacting the network."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(
        message="fictional-network-detail", request=request
    )


@pytest.mark.parametrize(
    "response",
    [
        reply(block("text", text="fictional-refusal-text"), stop_reason="refusal"),
        "connection",
    ],
    ids=["refusal", "connection-error"],
)
def test_refusals_and_api_errors_stop_the_run_safely(response: object) -> None:
    """Declines and SDK errors become provider_error without their text."""
    claude, _ = provider(connection_error() if response == "connection" else response)
    report = run_agent(agent_view(), claude)
    assert agent(report)["stop_reason"] == "provider_error"
    text = render_agent_report(report)
    assert (
        "fictional-refusal-text" not in text and "fictional-network-detail" not in text
    )


def test_tool_definitions_must_cover_every_listed_tool() -> None:
    """A tool without a schema is refused rather than sent unchecked."""
    turn = AgentTurn(1, 0, 0, (ToolSpec("shell", "x", {}),), (), ())
    with pytest.raises(ValueError, match="no schema"):
        tool_definitions(turn)


def test_credentials_are_checked_before_a_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without credentials the model provider is refused with a clear error."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", "/nonexistent-fictional-home")
    with pytest.raises(AgentError, match="credentials are not configured"):
        select_provider("anthropic", Settings())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fictional-anthropic-key-0000")
    claude, limits = select_provider("anthropic", Settings(app_agent_max_seconds=120))
    assert claude.name == "anthropic:claude-opus-5-5"
    assert (
        limits.max_seconds == 120 and limits.max_steps == DEFAULT_AGENT_LIMITS.max_steps
    )
    assert has_credentials(cast(ClaudeProvider, claude))
    offline, offline_limits = select_provider("offline", Settings())
    assert isinstance(offline, OfflineRulesProvider)
    assert offline_limits == DEFAULT_AGENT_LIMITS


def test_factory_passes_model_effort_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit settings reach the SDK client and the requests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fictional-anthropic-key-0000")
    claude = create_claude_provider(
        model="claude-sonnet-5-5", effort="medium", timeout_seconds=12.5
    )
    assert claude.client is not None
    assert claude.client.timeout == 12.5 and claude.client.max_retries == 2
    assert claude.name == "anthropic:claude-sonnet-5-5"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("APP_AGENT_MODEL", "gpt-5"),
        ("APP_AGENT_MODEL", "claude opus"),
        ("APP_AGENT_EFFORT", "extreme"),
        ("APP_AGENT_MAX_SECONDS", "5"),
        ("APP_AGENT_REQUEST_TIMEOUT_SECONDS", "0"),
    ],
)
def test_agent_settings_are_validated(
    field: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model names, effort levels and time limits are checked at startup."""
    monkeypatch.setenv(field, value)
    with pytest.raises(ValidationError):
        Settings()


@pytest.fixture
def saved(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """Save the Stage 05 fixture and return the store and its ID."""
    from app.cli import main as save_main

    store = tmp_path / "store.sqlite3"
    fixture = (
        Path(__file__).resolve().parents[1] / "fixtures/stage05/after_deployment.json"
    )
    assert save_main([str(fixture), "--store", str(store)]) == 0
    yield store, "inv-3dec28b1f8811404869dc78b"


def test_agent_cli_refuses_the_model_provider_without_credentials(
    saved: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--provider anthropic fails before any request when no key is set."""
    store, identifier = saved
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", "/nonexistent-fictional-home")
    capsys.readouterr()
    code = agent_main([identifier, "--store", str(store), "--provider", "anthropic"])
    captured = capsys.readouterr()
    assert (code, captured.out) == (2, "")
    assert "credentials are not configured" in captured.err
    assert agent_main([identifier, "--store", str(store)]) == 0
