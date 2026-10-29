"""Encode prepared evidence as one canonical JSON document and decode it strictly."""

import json
from datetime import datetime
from typing import NoReturn, cast

from app.application.inputs import ORIGINS, Origin, PreparedInput
from app.domain import (
    Incident,
    Investigation,
    Observation,
    ObservationKind,
    Source,
    SourceKind,
)

INPUT_DOCUMENT = "ai-devops-agent.prepared-input"
INPUT_VERSION = 1


class SnapshotError(ValueError):
    """Reject a stored input document that is not exactly the supported format."""


def _canonical(value: object) -> str:
    """Serialize with one byte representation so equal inputs hash equally."""
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def encode_input(prepared: PreparedInput) -> str:
    """Write every field that report generation reads, in a canonical order."""
    incident = prepared.incident
    snapshot = Investigation(incident, prepared.sources, prepared.observations)
    return _canonical(
        {
            "document": INPUT_DOCUMENT,
            "version": INPUT_VERSION,
            "origin": prepared.origin,
            "history_repository": prepared.history_repository,
            "incident": {
                "incident_id": incident.incident_id,
                "service": incident.service,
                "starts_at": incident.starts_at.isoformat(),
                "ends_at": incident.ends_at.isoformat(),
            },
            "sources": [
                {
                    "source_id": source.source_id,
                    "service": source.service,
                    "kind": source.kind.value,
                }
                for source in snapshot.sources
            ],
            "observations": [
                {
                    "observation_id": event.observation_id,
                    "source_id": event.source_id,
                    "event_time": event.event_time.isoformat(),
                    "received_at": event.received_at.isoformat(),
                    "kind": event.kind.value,
                    "message": event.message,
                    "exit_code": event.exit_code,
                }
                for event in snapshot.timeline
            ],
            "collection": {
                "declared_complete": prepared.declared_complete,
                "declared_truncated": prepared.declared_truncated,
                "gaps": list(prepared.gaps),
                "truncated_observation_ids": sorted(prepared.truncated_observation_ids),
                "truncated_gap_count": prepared.truncated_gap_count,
                "redaction_count": prepared.redaction_count,
            },
        }
    )


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject repeated keys rather than letting a later value win silently."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("Stored input repeats a field.")
        result[key] = value
    return result


def _no_float(value: str) -> NoReturn:
    """Reject floating-point and nonfinite numbers, which the format never uses."""
    raise SnapshotError("Stored input contains a non-integer number.")


def _fields(value: object, names: set[str]) -> dict[str, object]:
    """Require a JSON object with exactly the documented keys."""
    if not isinstance(value, dict) or value.keys() != names:
        raise SnapshotError("Stored input has missing or unsupported fields.")
    return cast(dict[str, object], value)


def _items(value: object) -> list[object]:
    """Require a JSON array."""
    if not isinstance(value, list):
        raise SnapshotError("Stored input expected an array.")
    return cast(list[object], value)


def _text(value: object) -> str:
    """Require text with valid Unicode scalar values for portable stored evidence."""
    if not isinstance(value, str) or any(
        0xD800 <= ord(char) <= 0xDFFF for char in value
    ):
        raise SnapshotError("Stored input expected valid Unicode text.")
    return value


def _integer(value: object) -> int:
    """Require a JSON integer and reject booleans."""
    if type(value) is not int:
        raise SnapshotError("Stored input expected an integer.")
    return value


def _count(value: object) -> int:
    """Require a nonnegative JSON integer for a quality counter."""
    count = _integer(value)
    if count < 0:
        raise SnapshotError("Stored input has a negative counter.")
    return count


def _flag(value: object) -> bool:
    """Require a JSON boolean."""
    if type(value) is not bool:
        raise SnapshotError("Stored input expected a boolean.")
    return value


def _time(value: object) -> datetime:
    """Parse an ISO-8601 time; domain constructors require its UTC offset."""
    return datetime.fromisoformat(_text(value))


def _origin(value: object) -> Origin:
    """Accept only a supported evidence origin."""
    for origin in ORIGINS:
        if value == origin:
            return origin
    raise SnapshotError("Stored input has an unsupported origin.")


def _observation(value: object) -> Observation:
    """Rebuild one observation through its validating domain constructor."""
    item = _fields(
        value,
        {
            "observation_id",
            "source_id",
            "event_time",
            "received_at",
            "kind",
            "message",
            "exit_code",
        },
    )
    exit_code = item["exit_code"]
    return Observation(
        _text(item["observation_id"]),
        _text(item["source_id"]),
        _time(item["event_time"]),
        _time(item["received_at"]),
        ObservationKind(_text(item["kind"])),
        message=_text(item["message"]),
        exit_code=None if exit_code is None else _integer(exit_code),
    )


def _decode(payload: object) -> PreparedInput:
    """Convert the parsed document into validated domain and quality values."""
    item = _fields(
        payload,
        {
            "document",
            "version",
            "origin",
            "history_repository",
            "incident",
            "sources",
            "observations",
            "collection",
        },
    )
    if item["document"] != INPUT_DOCUMENT or _integer(item["version"]) != (
        INPUT_VERSION
    ):
        raise SnapshotError("Stored input has an unsupported document version.")
    incident_fields = _fields(
        item["incident"], {"incident_id", "service", "starts_at", "ends_at"}
    )
    incident = Incident(
        _text(incident_fields["incident_id"]),
        _text(incident_fields["service"]),
        _time(incident_fields["starts_at"]),
        _time(incident_fields["ends_at"]),
    )
    sources = tuple(
        Source(
            _text(source["source_id"]),
            _text(source["service"]),
            SourceKind(_text(source["kind"])),
        )
        for source in (
            _fields(value, {"source_id", "service", "kind"})
            for value in _items(item["sources"])
        )
    )
    observations = tuple(_observation(value) for value in _items(item["observations"]))
    collection = _fields(
        item["collection"],
        {
            "declared_complete",
            "declared_truncated",
            "gaps",
            "truncated_observation_ids",
            "truncated_gap_count",
            "redaction_count",
        },
    )
    complete = collection["declared_complete"]
    history = item["history_repository"]
    snapshot = Investigation(incident, sources, observations)
    return PreparedInput(
        incident=incident,
        sources=snapshot.sources,
        observations=snapshot.timeline,
        declared_complete=None if complete is None else _flag(complete),
        declared_truncated=_flag(collection["declared_truncated"]),
        gaps=tuple(_text(gap) for gap in _items(collection["gaps"])),
        truncated_observation_ids=tuple(
            _text(value) for value in _items(collection["truncated_observation_ids"])
        ),
        truncated_gap_count=_count(collection["truncated_gap_count"]),
        redaction_count=_count(collection["redaction_count"]),
        origin=_origin(item["origin"]),
        history_repository=None if history is None else _text(history),
    )


def decode_input(document: str) -> PreparedInput:
    """Parse one canonical stored input, rejecting any alternative byte encoding."""
    try:
        payload: object = json.loads(
            document,
            object_pairs_hook=_unique,
            parse_constant=_no_float,
            parse_float=_no_float,
        )
        prepared = _decode(payload)
    except SnapshotError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise SnapshotError("Stored input is not a valid evidence snapshot.") from None
    if encode_input(prepared) != document:
        raise SnapshotError("Stored input is not in canonical form.")
    return prepared
