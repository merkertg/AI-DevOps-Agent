"""Run the LangGraph agent: limits, provider failures, injections and determinism."""

import socket
from dataclasses import replace
from typing import cast

import pytest

from app.agent.contracts import AgentLimits, AgentTurn
from app.agent.graph import render_agent_report, run_agent
from app.agent.offline import OfflineRulesProvider
from app.agent.tools import build_view
from app.infrastructure.fixtures import load_fixture

from agent_support import (
    DEPLOYMENT,
    FIXTURE,
    INJECTION,
    ROOT,
    ScriptedProvider,
    agent_view,
    call,
    finish,
)

GOOD = {"statement": "Fictional statement.", "fact_ids": ["fact-002"]}


def agent(report: dict[str, object]) -> dict[str, object]:
    """Return the run summary of an agent report."""
    return cast(dict[str, object], report["agent"])


def tool_log(report: dict[str, object]) -> list[dict[str, object]]:
    """Return the tool log of an agent report."""
    return cast(list[dict[str, object]], report["tool_log"])


def test_offline_provider_answers_with_cited_hypotheses_and_a_check() -> None:
    """The offline run reads five tools, cites facts and proposes one check."""
    report = run_agent(agent_view(), OfflineRulesProvider())
    assert agent(report) | {"limits": None} == {
        "graph": "bounded_investigation",
        "provider": "offline-rules-v1",
        "status": "answered",
        "stop_reason": None,
        "steps": 6,
        "tool_calls": 5,
        "limits": None,
    }
    hypotheses = cast(list[dict[str, object]], report["hypotheses"])
    assert [item["fact_ids"] for item in hypotheses] == [
        ["fact-001", "fact-002"],
        ["fact-002"],
        ["fact-003"],
    ]
    assert {item["status"] for item in hypotheses} == {"unconfirmed"}
    check = cast(dict[str, object], report["next_check"])
    assert (check["kind"], check["target"]) == ("compare_deployed_revision", DEPLOYMENT)
    assert report["rejected_hypotheses"] == [] and report["insufficient_data"] == []


def test_offline_report_is_byte_identical_across_runs() -> None:
    """The same investigation and provider reproduce the same report."""
    first = render_agent_report(run_agent(agent_view(), OfflineRulesProvider()))
    second = render_agent_report(run_agent(agent_view(), OfflineRulesProvider()))
    assert first == second
    assert first.isascii()


def test_injected_log_text_does_not_change_the_offline_decisions() -> None:
    """A log line that looks like a command leaves tools and answer unchanged."""
    clean = run_agent(agent_view("Fictional ordinary note."), OfflineRulesProvider())
    injected = run_agent(agent_view(INJECTION), OfflineRulesProvider())
    assert tool_log(clean) == tool_log(injected)
    assert clean["hypotheses"] == injected["hypotheses"]
    assert clean["next_check"] == injected["next_check"]


def test_offline_provider_on_partial_evidence_states_the_gaps() -> None:
    """Partial evidence still yields a cited hypothesis and visible gaps."""
    view = build_view(load_fixture(ROOT / "fixtures/stage03/partial_incident.json"))
    report = run_agent(view, OfflineRulesProvider())
    check = cast(dict[str, object], report["next_check"])
    assert check["kind"] == "review_dependency_endpoint"
    assert "Evidence quality is partial; findings may change with more data." in cast(
        list[str], report["insufficient_data"]
    )


def test_offline_provider_without_facts_asks_for_missing_evidence() -> None:
    """With only a note, nothing is hypothesized and missing data is requested."""
    prepared = load_fixture(FIXTURE)
    notes_only = replace(prepared, observations=prepared.observations[:1])
    report = run_agent(build_view(notes_only), OfflineRulesProvider())
    assert report["hypotheses"] == []
    check = cast(dict[str, object], report["next_check"])
    assert (check["kind"], check["target"]) == (
        "collect_missing_source",
        "api-container",
    )
    assert "No deterministic facts are available to cite." in cast(
        list[str], report["insufficient_data"]
    )


def test_step_limit_stops_a_provider_that_never_finishes() -> None:
    """A provider calling tools forever stops at the step limit."""
    limits = AgentLimits(max_steps=4, max_tool_calls=10)
    report = run_agent(
        agent_view(), ScriptedProvider(call("get_overview")), limits=limits
    )
    assert agent(report)["status"] == "stopped"
    assert agent(report)["stop_reason"] == "step_limit"
    assert (agent(report)["steps"], agent(report)["tool_calls"]) == (4, 4)
    assert report["hypotheses"] == []
    assert cast(list[str], report["insufficient_data"])[0] == (
        "The agent stopped at its step limit before a validated answer."
    )


def test_tool_call_limit_refuses_calls_and_tells_the_provider() -> None:
    """Calls over the budget are refused; the provider sees no calls remain."""
    limits = AgentLimits(max_steps=6, max_tool_calls=2)
    provider = ScriptedProvider(
        call("get_overview"),
        call("list_facts"),
        call("get_timeline"),
        finish(GOOD),
    )
    report = run_agent(agent_view(), provider, limits=limits)
    assert agent(report)["status"] == "answered"
    assert agent(report)["tool_calls"] == 2
    assert tool_log(report)[2] == {
        "step": 3,
        "tool": "get_timeline",
        "arguments": {},
        "status": "error",
        "error": "Tool call limit reached; finish with an answer.",
    }
    assert [turn.remaining_tool_calls for turn in provider.turns] == [2, 1, 0, 0]


def test_time_limit_is_checked_before_and_after_each_decision() -> None:
    """A slow provider is stopped by the deadline from the injected clock."""
    now = [0.0]

    def slow(turn: AgentTurn) -> object:
        """Advance the fake clock past the deadline while deciding."""
        now[0] += 31
        return call("get_overview")

    report = run_agent(agent_view(), ScriptedProvider(slow), clock=lambda: now[0])
    assert agent(report)["stop_reason"] == "time_limit"
    assert (agent(report)["steps"], agent(report)["tool_calls"]) == (1, 0)


@pytest.mark.parametrize(
    "error", [RuntimeError("fictional-provider-secret"), TimeoutError(), ValueError()]
)
def test_provider_errors_stop_the_run_without_leaking_details(
    error: Exception,
) -> None:
    """Exceptions from a provider become a stop reason with no message."""

    def failing(turn: AgentTurn) -> object:
        """Raise the configured provider error."""
        raise error

    report = run_agent(agent_view(), ScriptedProvider(failing))
    assert agent(report)["stop_reason"] == "provider_error"
    assert "fictional-provider-secret" not in render_agent_report(report)


def test_invalid_decisions_consume_steps_and_are_reported_back() -> None:
    """Malformed output is logged, shown to the provider and bounded by steps."""
    provider = ScriptedProvider({"action": "run_shell", "command": "id"}, finish(GOOD))
    report = run_agent(agent_view(), provider)
    assert agent(report)["status"] == "answered"
    assert tool_log(report)[0] == {
        "step": 1,
        "tool": "invalid_decision",
        "arguments": {},
        "status": "error",
        "error": "The action must be call_tool or finish.",
    }
    assert provider.turns[1].exchanges[0].tool == "invalid_decision"


@pytest.mark.parametrize(
    "tool", ["restart_container", "exec_in_container", "deploy", "rollback"]
)
def test_mutating_tool_requests_are_refused(tool: str) -> None:
    """Requests for nonexistent mutating tools are refused and not echoed."""
    provider = ScriptedProvider(
        call(tool, container="aidevops-lab04-api"), finish(GOOD)
    )
    report = run_agent(agent_view(), provider)
    assert tool_log(report)[0] == {
        "step": 1,
        "tool": "unknown_tool",
        "arguments": {},
        "status": "error",
        "error": "Unknown tool; only the listed read-only tools exist.",
    }
    assert "aidevops-lab04-api" not in render_agent_report(report)


def test_hypothesis_with_invented_reference_is_rejected_in_the_report() -> None:
    """The final report separates supported and unsupported hypotheses."""
    invented = {"statement": "Invented cause.", "fact_ids": ["fact-404"]}
    report = run_agent(agent_view(), ScriptedProvider(finish(GOOD, invented)))
    assert len(cast(list[object], report["hypotheses"])) == 1
    assert report["rejected_hypotheses"] == [
        {
            "index": 2,
            "statement": "Invented cause.",
            "reasons": ["unknown_fact_reference"],
        }
    ]


def test_provider_receives_the_catalog_and_budgets() -> None:
    """Each turn lists tools, check kinds and remaining budgets."""
    provider = ScriptedProvider(finish(GOOD))
    run_agent(agent_view(), provider)
    turn = provider.turns[0]
    assert (turn.step, turn.remaining_steps, turn.remaining_tool_calls) == (1, 11, 8)
    assert "get_timeline" in {spec.name for spec in turn.tools}
    assert "compare_deployed_revision" in turn.check_kinds


def test_run_opens_no_network_connection_even_with_tracing_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LangSmith tracing variables cannot send evidence off the machine."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "fictional-langsmith-key")
    attempts: list[object] = []

    def refuse(self: socket.socket, address: object) -> None:
        """Record and refuse every outgoing connection."""
        attempts.append(address)
        raise OSError("network disabled in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    report = run_agent(agent_view(), OfflineRulesProvider())
    assert agent(report)["status"] == "answered"
    assert attempts == []
