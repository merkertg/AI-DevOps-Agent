"""Carry an immutable, sanitized evidence snapshot into report generation."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from app.domain import (
    Incident,
    Investigation,
    Observation,
    ObservationKind,
    Source,
    SourceKind,
)

Origin = Literal["fixture", "docker_lab"]
ORIGINS: tuple[Origin, ...] = ("fixture", "docker_lab")
TELEMETRY_KINDS = ("metrics", "traces")
SKEW_TOLERANCE = timedelta(seconds=1)
MAX_LISTED = 10


@dataclass(frozen=True, slots=True)
class PreparedInput:
    """Retain normalized evidence and explicit producer/adapter quality metadata."""

    incident: Incident
    sources: tuple[Source, ...]
    observations: tuple[Observation, ...]
    declared_complete: bool | None = None
    declared_truncated: bool = False
    gaps: tuple[str, ...] = ()
    truncated_observation_ids: tuple[str, ...] = ()
    truncated_gap_count: int = 0
    redaction_count: int = 0
    origin: Origin = "fixture"
    history_repository: str | None = None
    telemetry: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Snapshot caller collections and require a known evidence origin."""
        for name in (
            "sources",
            "observations",
            "gaps",
            "truncated_observation_ids",
            "telemetry",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.origin not in ORIGINS:
            raise ValueError("origin must be a supported evidence origin")
        if list(self.telemetry) != sorted(set(self.telemetry)) or not set(
            self.telemetry
        ) <= set(TELEMETRY_KINDS):
            raise ValueError("telemetry must list metrics and traces once, sorted")
        if self.history_repository is not None and (
            not isinstance(self.history_repository, str)
            or not self.history_repository.strip()
        ):
            raise ValueError("history_repository must be a non-empty string or None")


@dataclass(frozen=True, slots=True)
class DeploymentEvidence:
    """Hold sanitized deployment observations read from one allowlisted repository."""

    repository: str
    source: Source
    observations: tuple[Observation, ...] = ()
    gaps: tuple[str, ...] = ()
    truncated_observation_ids: tuple[str, ...] = ()
    redaction_count: int = 0

    def __post_init__(self) -> None:
        """Snapshot collections and require one deployment history source."""
        for name in ("observations", "gaps", "truncated_observation_ids"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.source.kind is not SourceKind.DEPLOYMENT_HISTORY:
            raise ValueError("deployment evidence requires a deployment history source")
        if any(event.source_id != self.source.source_id for event in self.observations):
            raise ValueError("deployment observations must use the history source")


def add_deployment_evidence(
    prepared: PreparedInput, evidence: DeploymentEvidence
) -> PreparedInput:
    """Merge deployment history into one snapshot and revalidate scope and identity."""
    if prepared.history_repository is not None:
        raise ValueError("deployment history was already added")
    snapshot = Investigation(
        prepared.incident,
        (*prepared.sources, evidence.source),
        (*prepared.observations, *evidence.observations),
    )
    return replace(
        prepared,
        sources=snapshot.sources,
        observations=snapshot.timeline,
        gaps=(*prepared.gaps, *evidence.gaps),
        truncated_observation_ids=tuple(
            sorted(
                (
                    *prepared.truncated_observation_ids,
                    *evidence.truncated_observation_ids,
                )
            )
        ),
        redaction_count=prepared.redaction_count + evidence.redaction_count,
        history_repository=evidence.repository,
    )


@dataclass(frozen=True, slots=True)
class SpanInterval:
    """Keep one span's trace ID and time interval for correlation checks."""

    trace_id: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class TelemetryEvidence:
    """Hold sanitized metric and trace observations plus spans read for checks."""

    kinds: tuple[str, ...]
    sources: tuple[Source, ...]
    observations: tuple[Observation, ...] = ()
    gaps: tuple[str, ...] = ()
    spans: tuple[SpanInterval, ...] = ()
    truncated_observation_ids: tuple[str, ...] = ()
    redaction_count: int = 0

    def __post_init__(self) -> None:
        """Snapshot collections and require known telemetry kinds and sources."""
        for name in (
            "kinds",
            "sources",
            "observations",
            "gaps",
            "spans",
            "truncated_observation_ids",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not self.kinds or not set(self.kinds) <= set(TELEMETRY_KINDS):
            raise ValueError("telemetry kinds must be metrics and/or traces")
        allowed = {SourceKind.METRICS, SourceKind.TRACES}
        if any(source.kind not in allowed for source in self.sources):
            raise ValueError("telemetry evidence uses metrics and traces sources")


def _listed(ids: list[str]) -> str:
    """Name up to MAX_LISTED identifiers and count the rest."""
    shown = ", ".join(ids[:MAX_LISTED])
    extra = len(ids) - MAX_LISTED
    return f"{shown} and {extra} more" if extra > 0 else shown


def trace_gaps(
    prepared: PreparedInput,
    spans: tuple[SpanInterval, ...],
    tolerance: timedelta = SKEW_TOLERANCE,
) -> list[str]:
    """Report failure logs without trace IDs, missing traces and clock skew."""
    log_sources = {
        source.source_id
        for source in prepared.sources
        if source.kind is SourceKind.APPLICATION_LOG
    }
    logs = [event for event in prepared.observations if event.source_id in log_sources]
    intervals: dict[str, tuple[datetime, datetime]] = {}
    for span in spans:
        start, end = intervals.get(span.trace_id, (span.starts_at, span.ends_at))
        intervals[span.trace_id] = (min(start, span.starts_at), max(end, span.ends_at))
    gaps: list[str] = []
    untraced = [
        event.observation_id
        for event in logs
        if event.kind is ObservationKind.CONNECTION_REFUSED and event.trace_id is None
    ]
    if untraced:
        gaps.append(
            f"{len(untraced)} failure log observations have no trace ID and could "
            f"not be linked to traces: {_listed(untraced)}."
        )
    missing: list[str] = []
    skewed: list[str] = []
    for event in logs:
        if event.trace_id is None:
            continue
        interval = intervals.get(event.trace_id)
        if interval is None:
            missing.append(event.observation_id)
            continue
        start, end = interval
        if event.event_time < start - tolerance:
            offset = (start - event.event_time).total_seconds()
        elif event.event_time > end + tolerance:
            offset = (event.event_time - end).total_seconds()
        else:
            continue
        skewed.append(f"{event.observation_id} ({offset:.3f} s)")
    if missing:
        gaps.append(
            f"{len(missing)} log observations name traces missing from the trace "
            f"export; spans may be delayed, sampled or lost: {_listed(missing)}."
        )
    if skewed:
        gaps.append(
            f"{len(skewed)} log observations fall outside their trace spans by more "
            f"than {tolerance.total_seconds():g} s; log and trace clocks may "
            f"disagree: {_listed(skewed)}."
        )
    return gaps


def add_telemetry_evidence(
    prepared: PreparedInput, evidence: TelemetryEvidence
) -> PreparedInput:
    """Merge metrics and traces, then add correlation gaps found in code."""
    if prepared.telemetry:
        raise ValueError("telemetry was already added")
    snapshot = Investigation(
        prepared.incident,
        (*prepared.sources, *evidence.sources),
        (*prepared.observations, *evidence.observations),
    )
    derived = trace_gaps(prepared, evidence.spans) if "traces" in evidence.kinds else []
    return replace(
        prepared,
        sources=snapshot.sources,
        observations=snapshot.timeline,
        gaps=(*prepared.gaps, *evidence.gaps, *derived),
        truncated_observation_ids=tuple(
            sorted(
                (
                    *prepared.truncated_observation_ids,
                    *evidence.truncated_observation_ids,
                )
            )
        ),
        redaction_count=prepared.redaction_count + evidence.redaction_count,
        telemetry=tuple(sorted(set(evidence.kinds))),
    )
