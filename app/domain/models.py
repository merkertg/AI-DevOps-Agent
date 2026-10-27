"""Represent bounded incident evidence with immutable, standard-library models."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class SourceKind(StrEnum):
    """Distinguish an application log stream from container state observations."""

    APPLICATION_LOG = "application_log"
    CONTAINER_STATE = "container_state"


class ObservationKind(StrEnum):
    """Name structured signals; free-form messages never select a rule."""

    CONNECTION_REFUSED = "connection_refused"
    CONTAINER_EXIT = "container_exit"
    NOTE = "note"


def _require_text(value: str, name: str) -> None:
    """Reject empty identifiers and statements without silently rewriting them."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _utc(value: datetime, name: str) -> datetime:
    """Require an explicit timezone and normalize one timestamp to UTC."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Incident:
    """Define one service and an inclusive, explicit investigation interval."""

    incident_id: str
    service: str
    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        """Validate identity and require a non-reversed UTC interval."""
        _require_text(self.incident_id, "incident_id")
        _require_text(self.service, "service")
        object.__setattr__(self, "starts_at", _utc(self.starts_at, "starts_at"))
        object.__setattr__(self, "ends_at", _utc(self.ends_at, "ends_at"))
        if self.starts_at > self.ends_at:
            raise ValueError("starts_at must not be after ends_at")


@dataclass(frozen=True, slots=True)
class Source:
    """Identify one logical evidence stream belonging to a single service."""

    source_id: str
    service: str
    kind: SourceKind

    def __post_init__(self) -> None:
        """Require meaningful identity and an explicitly typed source kind."""
        _require_text(self.source_id, "source_id")
        _require_text(self.service, "service")
        if not isinstance(self.kind, SourceKind):
            raise ValueError("kind must be a SourceKind")


@dataclass(frozen=True, slots=True)
class Observation:
    """Store a structured event and inert message with separate collection time."""

    observation_id: str
    source_id: str
    event_time: datetime
    received_at: datetime
    kind: ObservationKind
    message: str = ""
    exit_code: int | None = None

    def __post_init__(self) -> None:
        """Validate event timing and permit exit codes only for container exits."""
        _require_text(self.observation_id, "observation_id")
        _require_text(self.source_id, "source_id")
        if not isinstance(self.kind, ObservationKind):
            raise ValueError("kind must be an ObservationKind")
        if not isinstance(self.message, str):
            raise ValueError("message must be a string")
        object.__setattr__(self, "event_time", _utc(self.event_time, "event_time"))
        object.__setattr__(self, "received_at", _utc(self.received_at, "received_at"))
        if self.received_at < self.event_time:
            raise ValueError("received_at must not be before event_time")
        if self.kind is ObservationKind.CONTAINER_EXIT:
            if type(self.exit_code) is not int or not 0 <= self.exit_code <= 255:
                raise ValueError(
                    "a container exit requires an integer exit_code 0..255"
                )
        elif self.exit_code is not None:
            raise ValueError("exit_code is allowed only for a container exit")


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """Point to an observation whose existence is checked within its report."""

    observation_id: str

    def __post_init__(self) -> None:
        """Require an explicit observation identity instead of an empty citation."""
        _require_text(self.observation_id, "observation_id")


def _validate_finding(
    code: str, statement: str, evidence: tuple[EvidenceReference, ...]
) -> tuple[EvidenceReference, ...]:
    """Snapshot nonempty citations and reject duplicate or invalid references."""
    _require_text(code, "code")
    _require_text(statement, "statement")
    references = tuple(evidence)
    if not references or any(
        not isinstance(reference, EvidenceReference) for reference in references
    ):
        raise ValueError("evidence must contain EvidenceReference values")
    if len({reference.observation_id for reference in references}) != len(references):
        raise ValueError("evidence must not repeat an observation")
    return references


@dataclass(frozen=True, slots=True)
class Fact:
    """Describe a reported signal, without claiming its ultimate cause is known."""

    code: str
    statement: str
    evidence: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        """Require a meaningful statement backed by explicit observation links."""
        object.__setattr__(
            self,
            "evidence",
            _validate_finding(self.code, self.statement, self.evidence),
        )


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """Keep a possible explanation distinct from recorded evidence and facts."""

    code: str
    statement: str
    evidence: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        """Require supporting observations while retaining hypothesis semantics."""
        object.__setattr__(
            self,
            "evidence",
            _validate_finding(self.code, self.statement, self.evidence),
        )


@dataclass(frozen=True, slots=True)
class Contradiction:
    """Preserve incompatible reports without choosing an unproven winner."""

    code: str
    statement: str
    evidence: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        """Require at least two observations to describe a disagreement."""
        references = _validate_finding(self.code, self.statement, self.evidence)
        if len(references) < 2:
            raise ValueError("a contradiction requires at least two observations")
        object.__setattr__(self, "evidence", references)


def _timeline_key(observation: Observation) -> tuple[datetime, datetime, str, str]:
    """Break equal event times deterministically without using input order."""
    return (
        observation.event_time,
        observation.received_at,
        observation.source_id,
        observation.observation_id,
    )


@dataclass(frozen=True, slots=True)
class Investigation:
    """Hold a deterministic evidence snapshot and separate kinds of findings."""

    incident: Incident
    sources: tuple[Source, ...]
    timeline: tuple[Observation, ...]
    facts: tuple[Fact, ...] = ()
    hypotheses: tuple[Hypothesis, ...] = ()
    contradictions: tuple[Contradiction, ...] = ()

    def __post_init__(self) -> None:
        """Validate scope, signal compatibility and every finding's references."""
        sources = tuple(sorted(self.sources, key=lambda source: source.source_id))
        timeline = tuple(sorted(self.timeline, key=_timeline_key))
        facts = tuple(self.facts)
        hypotheses = tuple(self.hypotheses)
        contradictions = tuple(self.contradictions)
        if any(not isinstance(fact, Fact) for fact in facts):
            raise ValueError("facts must contain Fact values")
        if any(not isinstance(hypothesis, Hypothesis) for hypothesis in hypotheses):
            raise ValueError("hypotheses must contain Hypothesis values")
        if any(not isinstance(item, Contradiction) for item in contradictions):
            raise ValueError("contradictions must contain Contradiction values")
        source_map = {source.source_id: source for source in sources}
        if len(source_map) != len(sources):
            raise ValueError("duplicate source_id")
        if any(source.service != self.incident.service for source in sources):
            raise ValueError("source service is outside the incident scope")
        observation_ids = {event.observation_id for event in timeline}
        if len(observation_ids) != len(timeline):
            raise ValueError("duplicate observation_id")
        for event in timeline:
            source = source_map.get(event.source_id)
            if source is None:
                raise ValueError(f"unknown source_id: {event.source_id}")
            if not self.incident.starts_at <= event.event_time <= self.incident.ends_at:
                raise ValueError("event_time is outside the incident interval")
            if (
                event.kind is ObservationKind.CONTAINER_EXIT
                and source.kind is not SourceKind.CONTAINER_STATE
            ) or (
                event.kind is ObservationKind.CONNECTION_REFUSED
                and source.kind is not SourceKind.APPLICATION_LOG
            ):
                raise ValueError("observation kind is incompatible with source kind")
        findings: tuple[Fact | Hypothesis | Contradiction, ...] = (
            *facts,
            *hypotheses,
            *contradictions,
        )
        for finding in findings:
            for reference in finding.evidence:
                if reference.observation_id not in observation_ids:
                    raise ValueError(
                        f"unknown evidence observation_id: {reference.observation_id}"
                    )
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "timeline", timeline)
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "hypotheses", hypotheses)
        object.__setattr__(self, "contradictions", contradictions)
