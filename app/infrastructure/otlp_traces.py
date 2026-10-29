"""Read spans of one service from a bounded OTLP JSON lines trace export."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from app.domain import TRACE_ID
from app.infrastructure.telemetry import (
    SpanRecord,
    TelemetryLimits,
    TelemetryUnavailable,
    TraceExport,
    read_regular_file,
)

SPAN_ID = re.compile(r"(?!0{16})[0-9a-f]{16}\Z")
_NANOS = re.compile(r"[0-9]{1,20}\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ERROR_CODES = {2, "STATUS_CODE_ERROR"}
MAX_NAME_CHARS = 200


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject repeated keys instead of letting the last one win."""
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _items(value: object) -> list[dict[str, object]]:
    """Return a list of objects, or raise when the shape is wrong."""
    if not isinstance(value, list) or not all(isinstance(i, dict) for i in value):
        raise ValueError("expected a list of objects")
    return cast(list[dict[str, object]], value)


def _service(resource: object) -> str | None:
    """Return the resource's service.name string attribute, if present."""
    if not isinstance(resource, dict):
        return None
    for attribute in _items(resource.get("attributes", [])):
        value = attribute.get("value")
        if attribute.get("key") == "service.name" and isinstance(value, dict):
            name = value.get("stringValue")
            return name if isinstance(name, str) else None
    return None


def _time(value: object) -> datetime:
    """Convert a Unix nanosecond count (string or integer) to UTC microseconds."""
    if type(value) is int:
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError("timestamp must be an integer or string")
    if not _NANOS.fullmatch(text):
        raise ValueError("timestamp must be a nonnegative integer")
    return _EPOCH + timedelta(microseconds=int(text) // 1_000)


def _span(value: dict[str, object]) -> SpanRecord:
    """Validate one span's identifiers, times, name and status."""
    trace_id = value.get("traceId")
    span_id = value.get("spanId")
    name = value.get("name", "")
    if not isinstance(trace_id, str) or not TRACE_ID.fullmatch(trace_id.lower()):
        raise ValueError("invalid trace ID")
    if not isinstance(span_id, str) or not SPAN_ID.fullmatch(span_id.lower()):
        raise ValueError("invalid span ID")
    if not isinstance(name, str):
        raise ValueError("invalid span name")
    starts_at = _time(value.get("startTimeUnixNano"))
    ends_at = _time(value.get("endTimeUnixNano"))
    if ends_at < starts_at:
        raise ValueError("span ends before it starts")
    status = value.get("status", {})
    if not isinstance(status, dict):
        raise ValueError("invalid status")
    message = status.get("message", "")
    if not isinstance(message, str):
        raise ValueError("invalid status message")
    code = status.get("code", 0)
    return SpanRecord(
        trace_id=trace_id.lower(),
        span_id=span_id.lower(),
        name=name[:MAX_NAME_CHARS],
        starts_at=starts_at,
        ends_at=ends_at,
        error=isinstance(code, int | str) and code in _ERROR_CODES,
        status_message=message,
    )


class OtlpFileReader:
    """Read an OpenTelemetry Collector file export without trusting its content."""

    def __init__(self, path: Path) -> None:
        """Remember the export path chosen by the developer."""
        self._path = path

    def read_spans(self, service: str, limits: TelemetryLimits) -> TraceExport:
        """Return bounded spans of the service and counts of what was skipped."""
        try:
            data = read_regular_file(self._path, limits.max_trace_bytes)
            text = data.decode("utf-8")
        except (OSError, ValueError):
            raise TelemetryUnavailable(
                "The trace export was not found, is not a regular file, is not "
                "UTF-8 or exceeds its size limit; traces are unknown."
            ) from None
        lines = [line for line in text.splitlines() if line.strip()]
        gaps: list[str] = []
        if len(lines) > limits.max_trace_lines:
            gaps.append(
                f"The trace export has more than {limits.max_trace_lines} lines; "
                "later lines were not read."
            )
            lines = lines[: limits.max_trace_lines]
        spans: list[SpanRecord] = []
        malformed_lines = 0
        malformed_spans = 0
        overflow = False
        for line in lines:
            try:
                payload = json.loads(line, object_pairs_hook=_unique)
                resources = _items(
                    payload.get("resourceSpans") if isinstance(payload, dict) else None
                )
            except (ValueError, RecursionError):
                malformed_lines += 1
                continue
            for resource in resources:
                try:
                    if _service(resource.get("resource")) != service:
                        continue
                    scopes = _items(resource.get("scopeSpans", []))
                    raw_spans = [
                        span
                        for scope in scopes
                        for span in _items(scope.get("spans", []))
                    ]
                except ValueError:
                    malformed_spans += 1
                    continue
                for raw in raw_spans:
                    if len(spans) == limits.max_spans:
                        overflow = True
                        break
                    try:
                        spans.append(_span(raw))
                    except (ValueError, OverflowError):
                        malformed_spans += 1
        if malformed_lines:
            gaps.append(
                f"{malformed_lines} trace export lines were not valid OTLP JSON; "
                "they were skipped."
            )
        if malformed_spans:
            gaps.append(
                f"{malformed_spans} spans or resources were malformed; they were "
                "skipped."
            )
        if overflow:
            gaps.append(
                f"The trace export has more than {limits.max_spans} spans of the "
                "service; later spans were not read."
            )
        return TraceExport(tuple(spans), tuple(gaps))
