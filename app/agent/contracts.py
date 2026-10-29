"""Define agent limits, provider turns and strict parsing of untrusted decisions."""

import json
import re
from dataclasses import dataclass, fields
from typing import Protocol, cast

TOOL_NAME = re.compile(r"[a-z][a-z_]{0,47}\Z")
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
MAX_ARGUMENT_CHARS = 1_000


class AgentError(ValueError):
    """Refuse an agent run with a safe static message."""


class DecisionError(ValueError):
    """Reject one malformed provider decision; the run records it and continues."""


@dataclass(frozen=True, slots=True)
class AgentLimits:
    """Bound decisions, tool calls, wall-clock time and the size of every result."""

    max_steps: int = 12
    max_tool_calls: int = 8
    max_seconds: int = 30
    max_tool_result_chars: int = 8_000
    max_hypotheses: int = 5
    max_text_chars: int = 600
    max_notes: int = 5

    def __post_init__(self) -> None:
        """Reject booleans, nonintegers and nonpositive limits."""
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value <= 0:
                raise ValueError("Agent limits must be positive integers.")

    def as_json(self) -> dict[str, int]:
        """Expose the limits in reports."""
        return {field.name: getattr(self, field.name) for field in fields(self)}


DEFAULT_AGENT_LIMITS = AgentLimits()


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Describe one read-only tool and its accepted arguments to a provider."""

    name: str
    description: str
    parameters: dict[str, str]


@dataclass(frozen=True, slots=True)
class ToolExchange:
    """Record one requested action and the bounded result returned for it."""

    step: int
    tool: str
    arguments: dict[str, object]
    result: dict[str, object]


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """Give a provider everything it may use for its next decision."""

    step: int
    remaining_steps: int
    remaining_tool_calls: int
    tools: tuple[ToolSpec, ...]
    check_kinds: tuple[str, ...]
    exchanges: tuple[ToolExchange, ...]


class AgentProvider(Protocol):
    """Choose the next action; the returned document is untrusted."""

    @property
    def name(self) -> str:
        """Identify the provider in reports."""
        ...

    def decide(self, turn: AgentTurn) -> object:
        """Return one raw decision: a tool call or a final answer."""
        ...


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Request one tool by name with JSON-compatible arguments."""

    tool: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProposedHypothesis:
    """Carry a provider's hypothesis and its claimed references before validation."""

    statement: str
    fact_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProposedCheck:
    """Carry a provider's suggested diagnostic check before validation."""

    kind: str
    target: str
    reason: str


@dataclass(frozen=True, slots=True)
class FinalAnswer:
    """Carry a provider's final answer before deterministic validation."""

    hypotheses: tuple[ProposedHypothesis, ...]
    next_check: ProposedCheck | None
    notes: tuple[str, ...]


def _fields(value: object, required: set[str], optional: set[str]) -> dict[str, object]:
    """Require an object whose keys are exactly the required plus allowed ones."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DecisionError("Decision parts must be JSON objects.")
    mapping = cast(dict[str, object], value)
    if not required <= mapping.keys() or mapping.keys() - required - optional:
        raise DecisionError("Decision has missing or unsupported fields.")
    return mapping


def _text(value: object, limit: int) -> str:
    """Require nonempty text within the length limit."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DecisionError(f"Decision text must be 1..{limit} characters.")
    return value


def _references(value: object, limit: int) -> tuple[str, ...]:
    """Require a short list of unique identifier-shaped references."""
    if not isinstance(value, list) or len(value) > limit:
        raise DecisionError(f"References must be a list of at most {limit} items.")
    items = cast(list[object], value)
    if not all(isinstance(item, str) and REFERENCE.fullmatch(item) for item in items):
        raise DecisionError("References must be identifier strings.")
    references = tuple(cast(list[str], items))
    if len(set(references)) != len(references):
        raise DecisionError("References must not repeat.")
    return references


def _tool_call(raw: dict[str, object]) -> ToolCall:
    """Parse a tool request whose arguments stay small and JSON-compatible."""
    item = _fields(raw, {"action", "tool", "arguments"}, set())
    tool = item["tool"]
    if not isinstance(tool, str) or not TOOL_NAME.fullmatch(tool):
        raise DecisionError("Tool names are lowercase words joined by underscores.")
    value = item["arguments"]
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DecisionError("Tool arguments must be a JSON object.")
    arguments = cast(dict[str, object], value)
    try:
        encoded = json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError):
        raise DecisionError("Tool arguments must be JSON values.") from None
    if len(encoded) > MAX_ARGUMENT_CHARS:
        raise DecisionError("Tool arguments exceed the size limit.")
    return ToolCall(tool, arguments)


def _answer(raw: dict[str, object], limits: AgentLimits) -> FinalAnswer:
    """Parse a final answer within the hypothesis, note and text limits."""
    item = _fields(raw, {"action", "hypotheses"}, {"next_check", "notes"})
    hypotheses = item["hypotheses"]
    if not isinstance(hypotheses, list) or len(hypotheses) > limits.max_hypotheses:
        raise DecisionError(
            f"An answer has a list of at most {limits.max_hypotheses} hypotheses."
        )
    parsed: list[ProposedHypothesis] = []
    for value in cast(list[object], hypotheses):
        hypothesis = _fields(value, {"statement", "fact_ids"}, {"observation_ids"})
        parsed.append(
            ProposedHypothesis(
                statement=_text(hypothesis["statement"], limits.max_text_chars),
                fact_ids=_references(hypothesis["fact_ids"], 10),
                observation_ids=_references(hypothesis.get("observation_ids", []), 20),
            )
        )
    check = None
    if item.get("next_check") is not None:
        fields_ = _fields(item["next_check"], {"kind", "target", "reason"}, set())
        check = ProposedCheck(
            kind=_text(fields_["kind"], 64),
            target=_text(fields_["target"], 96),
            reason=_text(fields_["reason"], limits.max_text_chars),
        )
    notes = item.get("notes", [])
    if not isinstance(notes, list) or len(notes) > limits.max_notes:
        raise DecisionError(f"Notes must be a list of at most {limits.max_notes}.")
    return FinalAnswer(
        hypotheses=tuple(parsed),
        next_check=check,
        notes=tuple(_text(note, limits.max_text_chars) for note in notes),
    )


def parse_decision(raw: object, limits: AgentLimits) -> ToolCall | FinalAnswer:
    """Turn one untrusted provider document into a typed decision or reject it."""
    if not isinstance(raw, dict):
        raise DecisionError("A decision must be a JSON object.")
    document = cast(dict[str, object], raw)
    action = document.get("action")
    if action == "call_tool":
        return _tool_call(document)
    if action == "finish":
        return _answer(document, limits)
    raise DecisionError("The action must be call_tool or finish.")
