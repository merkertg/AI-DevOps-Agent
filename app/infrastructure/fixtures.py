"""Read one strict, bounded UTF-8 JSON fixture without exposing raw error data."""

import json
import os
import re
import stat
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import NoReturn, cast

from app.application.inputs import PreparedInput
from app.domain import (
    Incident,
    Investigation,
    Observation,
    ObservationKind,
    Source,
    SourceKind,
)
from app.infrastructure.redaction import redact_text, sanitize_text

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class FixtureError(ValueError):
    """Expose only safe static diagnostics for a rejected external fixture."""


@dataclass(frozen=True, slots=True)
class InputLimits:
    """Set finite file, record, text and investigation-window limits."""

    max_input_bytes: int = 1_048_576
    max_sources: int = 32
    max_observations: int = 1_000
    max_message_chars: int = 2_048
    max_incident_seconds: int = 86_400
    max_gaps: int = 32

    def __post_init__(self) -> None:
        """Reject booleans, nonintegers and nonpositive resource limits."""
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value <= 0:
                raise ValueError("Input limits must be positive integers.")


DEFAULT_LIMITS = InputLimits()


def _read_bounded(path: Path, limit: int) -> bytes:
    """Read at most limit plus one byte, rejecting links and nonregular devices."""
    try:
        if path.is_symlink():
            raise FixtureError("Input must be a regular file, not a symbolic link.")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise FixtureError("Input must be a regular file.")
            if metadata.st_size > limit:
                raise FixtureError("Input exceeds the byte limit.")
            data = handle.read(limit + 1)
            if len(data) > limit:
                raise FixtureError("Input exceeds the byte limit.")
            return data
    except (OSError, TypeError, ValueError) as error:
        if isinstance(error, FixtureError):
            raise
        raise FixtureError("Input must be a readable regular file.") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject repeated JSON keys instead of silently overwriting earlier evidence."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureError("JSON object contains a duplicate field.")
        result[key] = value
    return result


def _reject_number(value: str) -> NoReturn:
    """Reject nonfinite constants and floats, which have no fixture schema fields."""
    raise FixtureError("JSON numbers must be finite integers.")


def _object(
    value: object, required: set[str], optional: set[str] | None = None
) -> dict[str, object]:
    """Require an object with exactly the documented required and optional keys."""
    if not isinstance(value, dict):
        raise FixtureError("Expected a JSON object.")
    mapping = cast(dict[str, object], value)
    if not required <= mapping.keys() or mapping.keys() - (
        required | (optional or set())
    ):
        raise FixtureError("JSON object has missing or unsupported fields.")
    return mapping


def _list(value: object, limit: int) -> list[object]:
    """Require a bounded record list rather than truncating evidence silently."""
    if not isinstance(value, list):
        raise FixtureError("Expected a JSON array.")
    if len(value) > limit:
        raise FixtureError("Input exceeds a record count limit.")
    return cast(list[object], value)


def _text(value: object) -> str:
    """Require text with valid Unicode scalar values for portable report output."""
    if not isinstance(value, str) or any(
        0xD800 <= ord(char) <= 0xDFFF for char in value
    ):
        raise FixtureError("Expected valid Unicode text.")
    return value


def _identifier(value: object) -> str:
    """Normalize safe metadata slugs while rejecting recognizable sensitive content."""
    text = _text(value).strip()
    if not IDENTIFIER.fullmatch(text) or redact_text(text)[0] != text:
        raise FixtureError("Identifiers must be safe ASCII slugs of 1..64 characters.")
    return text


def _time(value: object) -> datetime:
    """Parse an ISO-8601 timestamp; domain constructors require its UTC offset."""
    return datetime.fromisoformat(_text(value).strip())


def _sanitize(value: object, limit: int) -> tuple[str, int, bool]:
    """Require fixture text, then redact it before applying the visible bound."""
    return sanitize_text(_text(value), limit)


def _incident(value: object, limits: InputLimits) -> Incident:
    """Build one incident and enforce the maximum investigation duration."""
    item = _object(value, {"incident_id", "service", "starts_at", "ends_at"})
    incident = Incident(
        _identifier(item["incident_id"]),
        _identifier(item["service"]),
        _time(item["starts_at"]),
        _time(item["ends_at"]),
    )
    if (
        incident.ends_at - incident.starts_at
    ).total_seconds() > limits.max_incident_seconds:
        raise FixtureError("Incident exceeds the duration limit.")
    return incident


def _source(value: object) -> Source:
    """Convert a fixture source's exact kind value into a domain enum."""
    item = _object(value, {"source_id", "service", "kind"})
    return Source(
        _identifier(item["source_id"]),
        _identifier(item["service"]),
        SourceKind(_text(item["kind"])),
    )


def _observation(value: object, limits: InputLimits) -> tuple[Observation, int, bool]:
    """Normalize one observation while retaining redaction and truncation metadata."""
    item = _object(
        value,
        {"observation_id", "source_id", "event_time", "received_at", "kind"},
        {"message", "exit_code", "trace_id"},
    )
    message, redactions, truncated = _sanitize(
        item.get("message", ""), limits.max_message_chars
    )
    exit_code = item.get("exit_code")
    if exit_code is not None and type(exit_code) is not int:
        raise FixtureError("Exit codes must be integers or omitted.")
    trace_id = item.get("trace_id")
    observation = Observation(
        _identifier(item["observation_id"]),
        _identifier(item["source_id"]),
        _time(item["event_time"]),
        _time(item["received_at"]),
        ObservationKind(_text(item["kind"])),
        message=message,
        exit_code=exit_code,
        trace_id=None if trace_id is None else _text(trace_id),
    )
    return observation, redactions, truncated


def _collection(
    value: object, limits: InputLimits
) -> tuple[bool | None, bool, tuple[str, ...], int, int]:
    """Preserve producer declarations and sanitize each explicitly reported gap."""
    item = _object(value, set(), {"complete", "truncated", "gaps"})
    complete = item.get("complete")
    truncated = item.get("truncated", False)
    if ("complete" in item and type(complete) is not bool) or type(
        truncated
    ) is not bool:
        raise FixtureError("Collection declarations must be booleans.")
    gaps: list[str] = []
    redactions = 0
    truncated_gaps = 0
    for value in _list(item.get("gaps", []), limits.max_gaps):
        gap, count, shortened = _sanitize(value, limits.max_message_chars)
        gaps.append(gap)
        redactions += count
        truncated_gaps += int(shortened)
    return (
        cast(bool | None, complete),
        truncated,
        tuple(gaps),
        redactions,
        truncated_gaps,
    )


def _prepare(value: object, limits: InputLimits) -> PreparedInput:
    """Convert strict fields and check domain scope and reference constraints."""
    item = _object(
        value, {"schema_version", "incident", "sources", "observations"}, {"collection"}
    )
    if type(item["schema_version"]) is not int or item["schema_version"] != 1:
        raise FixtureError("Unsupported fixture schema version.")
    incident = _incident(item["incident"], limits)
    sources = tuple(
        _source(source) for source in _list(item["sources"], limits.max_sources)
    )
    observations: list[Observation] = []
    truncated_ids: list[str] = []
    redactions = 0
    for record in _list(item["observations"], limits.max_observations):
        observation, count, truncated = _observation(record, limits)
        observations.append(observation)
        redactions += count
        if truncated:
            truncated_ids.append(observation.observation_id)
    complete, upstream_truncated, gaps, gap_redactions, truncated_gaps = _collection(
        item.get("collection", {}), limits
    )
    snapshot = Investigation(incident, sources, tuple(observations))
    return PreparedInput(
        incident=incident,
        sources=snapshot.sources,
        observations=snapshot.timeline,
        declared_complete=complete,
        declared_truncated=upstream_truncated,
        gaps=gaps,
        truncated_observation_ids=tuple(sorted(truncated_ids)),
        truncated_gap_count=truncated_gaps,
        redaction_count=redactions + gap_redactions,
    )


def load_fixture(path: Path, limits: InputLimits = DEFAULT_LIMITS) -> PreparedInput:
    """Load bounded fictional JSON and turn parser/domain failures into safe errors."""
    try:
        text = _read_bounded(path, limits.max_input_bytes).decode("utf-8")
        payload: object = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
            parse_float=_reject_number,
        )
        return _prepare(payload, limits)
    except FixtureError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError, OSError):
        raise FixtureError(
            "Fixture encoding, JSON or incident evidence is invalid."
        ) from None
