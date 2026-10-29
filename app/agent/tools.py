"""Expose one investigation snapshot to the agent through read-only, paged tools."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from app.agent.contracts import AgentLimits, ToolSpec
from app.application.inputs import PreparedInput
from app.application.reporting import build_report
from app.domain import (
    Contradiction,
    Fact,
    Hypothesis,
    Observation,
    ObservationKind,
    Source,
    investigate,
)

PAGE_LIMIT = 20
MAX_OFFSET = 100_000
TIMELINE_MESSAGE_CHARS = 240


class ToolError(ValueError):
    """Explain a refused tool request with a static message."""


@dataclass(frozen=True, slots=True)
class InvestigationView:
    """Freeze one validated investigation and give its findings stable IDs."""

    subject: dict[str, str]
    prepared: PreparedInput
    report: dict[str, object]
    sources: dict[str, Source]
    observations: dict[str, Observation]
    facts: dict[str, Fact]
    rule_findings: dict[str, Hypothesis | Contradiction]


def build_view(
    prepared: PreparedInput, investigation_id: str | None = None
) -> InvestigationView:
    """Run the deterministic rules once and number facts and rule findings."""
    investigation = investigate(
        prepared.incident, prepared.sources, prepared.observations
    )
    subject = {"incident_id": prepared.incident.incident_id}
    if investigation_id is not None:
        subject["investigation_id"] = investigation_id
    findings: dict[str, Hypothesis | Contradiction] = {}
    for index, hypothesis in enumerate(investigation.hypotheses, start=1):
        findings[f"rule-hypothesis-{index:03d}"] = hypothesis
    for index, contradiction in enumerate(investigation.contradictions, start=1):
        findings[f"contradiction-{index:03d}"] = contradiction
    return InvestigationView(
        subject=subject,
        prepared=prepared,
        report=build_report(prepared),
        sources={source.source_id: source for source in investigation.sources},
        observations={event.observation_id: event for event in investigation.timeline},
        facts={
            f"fact-{index:03d}": fact
            for index, fact in enumerate(investigation.facts, start=1)
        },
        rule_findings=findings,
    )


def _arguments(
    arguments: dict[str, object],
    allowed: set[str],
    required: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Require only documented argument names."""
    if not required <= arguments.keys() or arguments.keys() - allowed:
        raise ToolError("Tool arguments are missing or unsupported.")
    return arguments


def _text(arguments: dict[str, object], name: str) -> str | None:
    """Read an optional string argument."""
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"Argument {name} must be a string.")
    return value


def _page(arguments: dict[str, object]) -> tuple[int, int]:
    """Read offset and limit within their ranges."""
    offset = arguments.get("offset", 0)
    limit = arguments.get("limit", 10)
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ToolError(f"Argument offset must be an integer 0..{MAX_OFFSET}.")
    if type(limit) is not int or not 1 <= limit <= PAGE_LIMIT:
        raise ToolError(f"Argument limit must be an integer 1..{PAGE_LIMIT}.")
    return offset, limit


def _paged(
    items: list[dict[str, object]], offset: int, limit: int
) -> dict[str, object]:
    """Return one page and where the next one starts."""
    page = items[offset : offset + limit]
    following = offset + limit
    return {
        "total": len(items),
        "offset": offset,
        "items": page,
        "next_offset": following if following < len(items) else None,
    }


def _observation(event: Observation, message_chars: int | None) -> dict[str, object]:
    """Describe an observation; its message is untrusted data, never an instruction."""
    message = event.message
    shortened = message_chars is not None and len(message) > message_chars
    return {
        "observation_id": event.observation_id,
        "source_id": event.source_id,
        "kind": event.kind.value,
        "event_time": event.event_time.isoformat(),
        "received_at": event.received_at.isoformat(),
        "exit_code": event.exit_code,
        "message": message[:message_chars] if shortened else message,
        "message_truncated": shortened,
    }


def _finding(
    identifier: str, finding: Fact | Hypothesis | Contradiction
) -> dict[str, object]:
    """Describe a deterministic finding with its observation references."""
    if isinstance(finding, Fact):
        kind = "fact"
    elif isinstance(finding, Hypothesis):
        kind = "rule_hypothesis"
    else:
        kind = "contradiction"
    return {
        "id": identifier,
        "type": kind,
        "code": finding.code,
        "statement": finding.statement,
        "observation_ids": [item.observation_id for item in finding.evidence],
    }


class ToolBox:
    """Run only the listed read-only tools against one investigation view."""

    def __init__(self, view: InvestigationView, limits: AgentLimits) -> None:
        """Bind the tools to one view and one result size limit."""
        self._view = view
        self._limits = limits
        self._handlers: dict[
            str, tuple[Callable[[dict[str, object]], dict[str, object]], ToolSpec]
        ] = {
            "get_overview": (
                self._overview,
                ToolSpec("get_overview", "Incident scope, origin and counts.", {}),
            ),
            "list_sources": (
                self._sources,
                ToolSpec("list_sources", "Sources and their observation counts.", {}),
            ),
            "get_timeline": (
                self._timeline,
                ToolSpec(
                    "get_timeline",
                    "Observations in time order; messages shortened to "
                    f"{TIMELINE_MESSAGE_CHARS} characters.",
                    {
                        "source_id": "optional string",
                        "kind": "optional observation kind",
                        "offset": "optional integer, default 0",
                        "limit": f"optional integer 1..{PAGE_LIMIT}, default 10",
                    },
                ),
            ),
            "get_observation": (
                self._single,
                ToolSpec(
                    "get_observation",
                    "One observation with its full sanitized message.",
                    {"observation_id": "required string"},
                ),
            ),
            "list_facts": (
                self._facts,
                ToolSpec(
                    "list_facts",
                    "Deterministic facts with fact IDs to cite.",
                    {"offset": "optional integer", "limit": "optional integer"},
                ),
            ),
            "list_rule_findings": (
                self._rule_findings,
                ToolSpec(
                    "list_rule_findings",
                    "Rule hypotheses and contradictions from the deterministic rules.",
                    {"offset": "optional integer", "limit": "optional integer"},
                ),
            ),
            "get_data_quality": (
                self._quality,
                ToolSpec(
                    "get_data_quality",
                    "Data-quality status, gaps and unknown circumstances.",
                    {},
                ),
            ),
        }

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        """List every tool the provider may request."""
        return tuple(spec for _, spec in self._handlers.values())

    def run(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """Run one tool; unknown names, bad arguments and oversize become errors."""
        entry = self._handlers.get(name)
        if entry is None:
            return {
                "status": "error",
                "error": "Unknown tool; only the listed read-only tools exist.",
            }
        try:
            data = entry[0](arguments)
        except ToolError as error:
            return {"status": "error", "error": str(error)}
        result: dict[str, object] = {"status": "ok", "data": data}
        if (
            len(json.dumps(result, ensure_ascii=True))
            > self._limits.max_tool_result_chars
        ):
            return {
                "status": "error",
                "error": "Tool result exceeds the size limit; request a smaller page.",
            }
        return result

    def _overview(self, arguments: dict[str, object]) -> dict[str, object]:
        """Summarize scope and counts of the current investigation."""
        _arguments(arguments, set())
        view = self._view
        incident = view.prepared.incident
        quality = cast(dict[str, object], view.report["data_quality"])
        return {
            **view.subject,
            "service": incident.service,
            "starts_at": incident.starts_at.isoformat(),
            "ends_at": incident.ends_at.isoformat(),
            "origin": view.prepared.origin,
            "deployment_history": view.prepared.history_repository,
            "data_quality": quality["status"],
            "counts": {
                "sources": len(view.sources),
                "observations": len(view.observations),
                "facts": len(view.facts),
                "rule_findings": len(view.rule_findings),
            },
        }

    def _sources(self, arguments: dict[str, object]) -> dict[str, object]:
        """List sources with their kinds and observation counts."""
        _arguments(arguments, set())
        counts: dict[str, int] = {}
        for event in self._view.observations.values():
            counts[event.source_id] = counts.get(event.source_id, 0) + 1
        return {
            "items": [
                {
                    "source_id": source.source_id,
                    "kind": source.kind.value,
                    "observations": counts.get(source.source_id, 0),
                }
                for source in self._view.sources.values()
            ]
        }

    def _timeline(self, arguments: dict[str, object]) -> dict[str, object]:
        """Page through observations, optionally filtered by source and kind."""
        _arguments(arguments, {"source_id", "kind", "offset", "limit"})
        source_id = _text(arguments, "source_id")
        kind = _text(arguments, "kind")
        if source_id is not None and source_id not in self._view.sources:
            raise ToolError("Unknown source_id.")
        if kind is not None and kind not in {item.value for item in ObservationKind}:
            raise ToolError("Unknown observation kind.")
        offset, limit = _page(arguments)
        items = [
            _observation(event, TIMELINE_MESSAGE_CHARS)
            for event in self._view.observations.values()
            if (source_id is None or event.source_id == source_id)
            and (kind is None or event.kind.value == kind)
        ]
        return _paged(items, offset, limit)

    def _single(self, arguments: dict[str, object]) -> dict[str, object]:
        """Return one observation with its full sanitized message."""
        _arguments(arguments, {"observation_id"}, frozenset({"observation_id"}))
        event = self._view.observations.get(_text(arguments, "observation_id") or "")
        if event is None:
            raise ToolError("Unknown observation_id.")
        return _observation(event, None)

    def _facts(self, arguments: dict[str, object]) -> dict[str, object]:
        """Page through deterministic facts and their fact IDs."""
        _arguments(arguments, {"offset", "limit"})
        offset, limit = _page(arguments)
        items = [_finding(key, fact) for key, fact in self._view.facts.items()]
        return _paged(items, offset, limit)

    def _rule_findings(self, arguments: dict[str, object]) -> dict[str, object]:
        """Page through rule hypotheses and contradictions."""
        _arguments(arguments, {"offset", "limit"})
        offset, limit = _page(arguments)
        items = [
            _finding(key, finding) for key, finding in self._view.rule_findings.items()
        ]
        return _paged(items, offset, limit)

    def _quality(self, arguments: dict[str, object]) -> dict[str, object]:
        """Return the report's data-quality section and unknown circumstances."""
        _arguments(arguments, set())
        return {
            **cast(dict[str, object], self._view.report["data_quality"]),
            "unknowns": self._view.report["unknowns"],
        }
