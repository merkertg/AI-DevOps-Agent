"""Collect one bounded evidence snapshot from allowlisted Stage 04 lab containers."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.application.inputs import PreparedInput
from app.domain import (
    TRACE_ID,
    Incident,
    Investigation,
    Observation,
    ObservationKind,
    Source,
    SourceKind,
)
from app.infrastructure.docker_engine import (
    CONTAINER_NAME,
    ContainerSnapshot,
    LogBatch,
)
from app.infrastructure.fixtures import IDENTIFIER
from app.infrastructure.redaction import redact_text, sanitize_text

# Only an explicit structured event field selects a signal. Container exits come
# from Docker state, never from log text, so a log line cannot forge an exit code.
_LOG_SIGNALS = {"connection_refused": ObservationKind.CONNECTION_REFUSED}
_FINISHED = frozenset({"exited", "dead"})


class CollectionError(ValueError):
    """Reject an out-of-scope or invalid collection request with a safe message."""


@dataclass(frozen=True, slots=True)
class LabScope:
    """Name the only lab label, service and containers the collector may read."""

    lab: str
    service: str
    containers: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require Docker-safe names so derived source IDs stay valid and short."""
        if not self.containers or any(
            not CONTAINER_NAME.fullmatch(name) for name in self.containers
        ):
            raise ValueError("Lab containers must be Docker-safe slugs.")
        object.__setattr__(self, "containers", tuple(self.containers))


# Changing what the collector may read is a reviewed code change (ADR 0002).
STAGE04_LAB = LabScope("stage04", "training-api", ("aidevops-lab04-api",))
# Stage 07 adds metrics and traces; only its application container is read.
STAGE07_LAB = LabScope("stage07", "training-api", ("aidevops-lab07-api",))
LABS = {scope.lab: scope for scope in (STAGE04_LAB, STAGE07_LAB)}


@dataclass(frozen=True, slots=True)
class CollectionLimits:
    """Bound targets, window, log volume, response size and request time."""

    max_targets: int = 4
    max_window_minutes: int = 1_440
    max_log_lines: int = 500
    max_log_bytes: int = 1_048_576
    max_inspect_bytes: int = 262_144
    max_message_chars: int = 2_048
    timeout_seconds: int = 5

    def __post_init__(self) -> None:
        """Reject booleans, nonintegers and nonpositive resource limits."""
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value <= 0:
                raise ValueError("Collection limits must be positive integers.")


DEFAULT_COLLECTION_LIMITS = CollectionLimits()


class ContainerReader(Protocol):
    """Read selected state and bounded logs for one allowlisted container."""

    def inspect(self, name: str) -> ContainerSnapshot | None:
        """Return selected state fields, or None when the container is absent."""
        ...

    def logs(
        self,
        name: str,
        *,
        since: datetime,
        until: datetime,
        max_lines: int,
        tty: bool,
    ) -> LogBatch | None:
        """Return the newest bounded log lines, or None when the container is gone."""
        ...


def select_targets(
    scope: LabScope, requested: Sequence[str], limits: CollectionLimits
) -> tuple[str, ...]:
    """Validate requested names against the allowlist before any Docker request."""
    targets = tuple(requested) or scope.containers
    if len(targets) > limits.max_targets:
        raise CollectionError("Too many containers were requested.")
    if len(set(targets)) != len(targets):
        raise CollectionError("Each container may be requested only once.")
    if any(name not in scope.containers for name in targets):
        raise CollectionError("A requested container is not in the lab allowlist.")
    return targets


def _signal(text: str) -> tuple[ObservationKind, str | None]:
    """Map a whole-line JSON object's exact event and trace_id fields only."""
    if not text.lstrip().startswith("{"):
        return ObservationKind.NOTE, None

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        """Treat repeated keys as ambiguous instead of letting the last one win."""
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate key")
        return dict(pairs)

    try:
        value: object = json.loads(text, object_pairs_hook=unique)
    except (ValueError, RecursionError):
        return ObservationKind.NOTE, None
    if not isinstance(value, dict):
        return ObservationKind.NOTE, None
    trace = value.get("trace_id")
    trace_id = trace if isinstance(trace, str) and TRACE_ID.fullmatch(trace) else None
    event = value.get("event")
    if not isinstance(event, str):
        return ObservationKind.NOTE, trace_id
    return _LOG_SIGNALS.get(event, ObservationKind.NOTE), trace_id


def _state_observation(
    snapshot: ContainerSnapshot,
    source_id: str,
    starts_at: datetime,
    collected_at: datetime,
) -> tuple[Observation, str | None]:
    """Describe selected Docker state as an exit event or a collection-time note."""
    code = snapshot.exit_code
    finished = snapshot.finished_at
    summary = (
        f"Docker state: status={snapshot.status} exit_code={code} "
        f"oom_killed={str(snapshot.oom_killed).lower()} "
        f"restart_count={snapshot.restart_count}"
    )
    valid_code = code is not None and 0 <= code <= 255
    if (
        snapshot.status in _FINISHED
        and finished is not None
        and starts_at <= finished <= collected_at
        and code is not None
        and valid_code
    ):
        return (
            Observation(
                f"{source_id}-exit",
                source_id,
                finished,
                collected_at,
                ObservationKind.CONTAINER_EXIT,
                message=f"{summary} finished_at={finished.isoformat()}.",
                exit_code=code,
            ),
            None,
        )
    gap = None
    if snapshot.status in _FINISHED and not valid_code:
        gap = f"Container {snapshot.name} reported an exit code outside 0..255."
    detail = f" finished_at={finished.isoformat()}" if finished is not None else ""
    return (
        Observation(
            f"{source_id}-snapshot",
            source_id,
            collected_at,
            collected_at,
            ObservationKind.NOTE,
            message=f"{summary}{detail}; observed at collection time.",
        ),
        gap,
    )


def _log_gaps(name: str, batch: LogBatch, limits: CollectionLimits) -> list[str]:
    """State every known loss in the log read instead of returning it silently."""
    gaps: list[str] = []
    if batch.truncated_lines:
        gaps.append(
            f"Container {name} had more than {limits.max_log_lines} log lines "
            "in the window; older lines were omitted."
        )
    if batch.truncated_bytes:
        gaps.append(
            f"Container {name} log response exceeded {limits.max_log_bytes} bytes; "
            "newer lines were omitted."
        )
    if batch.malformed_lines:
        gaps.append(
            f"Container {name} returned {batch.malformed_lines} log lines "
            "without a valid timestamp; they were skipped."
        )
    return gaps


def collect_lab(
    reader: ContainerReader,
    *,
    collected_at: datetime,
    window_minutes: int,
    requested: Sequence[str] = (),
    incident_id: str | None = None,
    scope: LabScope = STAGE04_LAB,
    limits: CollectionLimits = DEFAULT_COLLECTION_LIMITS,
) -> PreparedInput:
    """Read each allowlisted container once and build sanitized, bounded evidence."""
    targets = select_targets(scope, requested, limits)
    if type(window_minutes) is not int or not (
        1 <= window_minutes <= limits.max_window_minutes
    ):
        raise CollectionError("The collection window is outside the allowed range.")
    if collected_at.utcoffset() is None:
        raise CollectionError("Collection time must include a timezone.")
    ends_at = collected_at.astimezone(UTC)
    starts_at = ends_at - timedelta(minutes=window_minutes)
    if incident_id is None:
        incident_id = f"{scope.lab}-{ends_at:%Y%m%dT%H%M%SZ}"
    if not IDENTIFIER.fullmatch(incident_id) or redact_text(incident_id)[0] != (
        incident_id
    ):
        raise CollectionError("Incident ID must be a safe ASCII slug.")
    incident = Incident(incident_id, scope.service, starts_at, ends_at)

    sources: list[Source] = []
    observations: list[Observation] = []
    gaps: list[str] = []
    truncated_ids: list[str] = []
    redactions = 0
    truncated_input = False
    for name in targets:
        log_source = Source(f"{name}-log", scope.service, SourceKind.APPLICATION_LOG)
        state_source = Source(
            f"{name}-state", scope.service, SourceKind.CONTAINER_STATE
        )
        sources += [log_source, state_source]
        snapshot = reader.inspect(name)
        if snapshot is None:
            gaps.append(
                f"Container {name} was not found; its state and logs are missing."
            )
            continue
        if snapshot.name != name:
            raise CollectionError(
                "Docker returned a different container than requested."
            )
        if snapshot.lab_label != scope.lab:
            raise CollectionError(
                f"Container {name} is not labeled for lab {scope.lab}; it was not read."
            )
        state, state_gap = _state_observation(
            snapshot, state_source.source_id, incident.starts_at, incident.ends_at
        )
        observations.append(state)
        if state_gap:
            gaps.append(state_gap)
        batch = reader.logs(
            name,
            since=incident.starts_at,
            until=incident.ends_at,
            max_lines=limits.max_log_lines,
            tty=snapshot.tty,
        )
        if batch is None:
            gaps.append(f"Container {name} disappeared before its logs were read.")
            continue
        gaps += _log_gaps(name, batch, limits)
        truncated_input |= batch.truncated_lines or batch.truncated_bytes
        outside = 0
        line_number = 0
        for entry in batch.entries:
            if not incident.starts_at <= entry.timestamp <= incident.ends_at:
                outside += 1
                continue
            line_number += 1
            message, count, cut = sanitize_text(entry.text, limits.max_message_chars)
            observation_id = f"{log_source.source_id}-{line_number:04d}"
            kind, trace_id = _signal(entry.text)
            observations.append(
                Observation(
                    observation_id,
                    log_source.source_id,
                    entry.timestamp,
                    incident.ends_at,
                    kind,
                    message=message,
                    trace_id=trace_id,
                )
            )
            redactions += count
            if cut:
                truncated_ids.append(observation_id)
        if outside:
            gaps.append(
                f"Container {name} returned {outside} log lines outside the window; "
                "they were skipped because host and daemon clocks may disagree."
            )
    snapshot_check = Investigation(incident, tuple(sources), tuple(observations))
    return PreparedInput(
        incident=incident,
        sources=snapshot_check.sources,
        observations=snapshot_check.timeline,
        declared_complete=not gaps,
        declared_truncated=truncated_input,
        gaps=tuple(gaps),
        truncated_observation_ids=tuple(sorted(truncated_ids)),
        redaction_count=redactions,
        origin="docker_lab",
    )
