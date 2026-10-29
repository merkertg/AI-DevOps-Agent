"""Parse untrusted provider decisions strictly and validate agent limits."""

import pytest

from app.agent.contracts import (
    AgentLimits,
    DecisionError,
    FinalAnswer,
    ToolCall,
    parse_decision,
)

from agent_support import call, finish

LIMITS = AgentLimits()
HYPOTHESIS = {"statement": "Fictional statement.", "fact_ids": ["fact-001"]}


def test_tool_call_and_answer_parse_into_typed_decisions() -> None:
    """Well-formed documents become typed decisions with defaults filled."""
    assert parse_decision(call("get_timeline", limit=5), LIMITS) == ToolCall(
        "get_timeline", {"limit": 5}
    )
    answer = parse_decision(
        finish(
            HYPOTHESIS,
            next_check={"kind": "k", "target": "t", "reason": "r"},
            notes=["n"],
        ),
        LIMITS,
    )
    assert isinstance(answer, FinalAnswer)
    assert answer.hypotheses[0].observation_ids == ()
    assert answer.next_check is not None and answer.notes == ("n",)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "call get_overview",
        ["action", "finish"],
        {"action": "run_shell", "command": "restart"},
        {"tool": "get_overview", "arguments": {}},
        {"action": "call_tool", "tool": "get_overview"},
        {"action": "call_tool", "tool": "get_overview", "arguments": {}, "x": 1},
        {"action": "call_tool", "tool": "Get-Overview", "arguments": {}},
        {"action": "call_tool", "tool": "x" * 49, "arguments": {}},
        {"action": "call_tool", "tool": "get_overview", "arguments": []},
        {"action": "call_tool", "tool": "get_overview", "arguments": {1: 2}},
        {"action": "call_tool", "tool": "get_overview", "arguments": {"v": {1}}},
        {
            "action": "call_tool",
            "tool": "get_overview",
            "arguments": {"v": float("nan")},
        },
        {
            "action": "call_tool",
            "tool": "get_overview",
            "arguments": {"v": "x" * 1_000},
        },
        {"action": "finish"},
        {"action": "finish", "hypotheses": {}},
        {"action": "finish", "hypotheses": [HYPOTHESIS] * 6},
        {"action": "finish", "hypotheses": [{"statement": "s"}]},
        {"action": "finish", "hypotheses": [{**HYPOTHESIS, "confidence": 0.9}]},
        {"action": "finish", "hypotheses": [{**HYPOTHESIS, "statement": ""}]},
        {"action": "finish", "hypotheses": [{**HYPOTHESIS, "statement": "x" * 601}]},
        {"action": "finish", "hypotheses": [{**HYPOTHESIS, "fact_ids": "fact-001"}]},
        {"action": "finish", "hypotheses": [{**HYPOTHESIS, "fact_ids": [1]}]},
        {"action": "finish", "hypotheses": [{**HYPOTHESIS, "fact_ids": ["a b"]}]},
        {"action": "finish", "hypotheses": [{**HYPOTHESIS, "fact_ids": ["f", "f"]}]},
        {"action": "finish", "hypotheses": [{**HYPOTHESIS, "fact_ids": ["f"] * 11}]},
        {"action": "finish", "hypotheses": [], "next_check": {"kind": "k"}},
        {"action": "finish", "hypotheses": [], "notes": "note"},
        {"action": "finish", "hypotheses": [], "notes": ["n"] * 6},
        {"action": "finish", "hypotheses": [], "summary": "extra"},
    ],
)
def test_malformed_decisions_are_rejected(raw: object) -> None:
    """Wrong shapes, types, sizes and extra fields never become decisions."""
    with pytest.raises(DecisionError):
        parse_decision(raw, LIMITS)


def test_answer_limits_follow_the_configured_values() -> None:
    """Hypothesis count and text length use the run's limits."""
    small = AgentLimits(max_hypotheses=1, max_text_chars=10)
    with pytest.raises(DecisionError):
        parse_decision(finish(HYPOTHESIS, HYPOTHESIS), small)
    with pytest.raises(DecisionError):
        parse_decision(finish({"statement": "x" * 11, "fact_ids": []}), small)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3"])
def test_limits_must_be_positive_integers(value: object) -> None:
    """Every limit rejects zero, negatives, booleans and non-integers."""
    with pytest.raises(ValueError):
        AgentLimits(max_steps=value)  # type: ignore[arg-type]
