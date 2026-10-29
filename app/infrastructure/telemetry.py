"""Map allowlisted metric queries and a bounded trace export to incident evidence."""

import math
import os
import stat
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Protocol

from app.application.inputs import SpanInterval, TelemetryEvidence
from app.domain import Incident, Observation, ObservationKind, Source, SourceKind
from app.infrastructure.redaction import sanitize_text


class TelemetryError(ValueError):
    """Refuse an out-of-scope telemetry request with a safe static message."""


class TelemetryUnavailable(Exception):
    """Carry a safe gap text explaining why telemetry could not be read."""


@dataclass(frozen=True, slots=True)
class MetricQuery:
    """Name one fixed PromQL expression and the threshold that makes a signal."""

    name: str
    expression: str
    threshold: float
    description: str


@dataclass(frozen=True, slots=True)
class TelemetryScope:
    """Fix the service, its telemetry sources and the only allowed queries."""

    service: str
    queries: tuple[MetricQuery, ...]
    step_seconds: int = 15
    stale_after_seconds: int = 60

    @property
    def metrics_source(self) -> str:
        """Return the source ID used for metric observations."""
        return f"{self.service}-metrics"

    @property
    def traces_source(self) -> str:
        """Return the source ID used for trace observations."""
        return f"{self.service}-traces"


# Changing what may be queried is a reviewed code change (ADR 0006). No query
# text comes from a command line, a log line or an agent.
STAGE07_TELEMETRY = TelemetryScope(
    service="training-api",
    queries=(
        MetricQuery(
            "dependency_failures",
            'increase(training_api_dependency_failures_total{job="training-api"}[1m])',
            0.0,
            "failed dependency connections in the previous minute",
        ),
        MetricQuery(
            "target_down",
            '1 - up{job="training-api"}',
            0.0,
            "Prometheus could not scrape the service",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class TelemetryLimits:
    """Bound metric points, response sizes, request time, spans and messages."""

    max_points: int = 500
    max_response_bytes: int = 1_048_576
    timeout_seconds: int = 5
    max_signals_per_query: int = 20
    max_trace_bytes: int = 4_194_304
    max_trace_lines: int = 10_000
    max_spans: int = 2_000
    max_error_spans: int = 200
    max_message_chars: int = 2_048

    def __post_init__(self) -> None:
        """Reject booleans, nonintegers and nonpositive limits."""
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value <= 0:
                raise ValueError("Telemetry limits must be positive integers.")


DEFAULT_TELEMETRY_LIMITS = TelemetryLimits()


@dataclass(frozen=True, slots=True)
class MetricSample:
    """Hold one sample time and value of a metric series."""

    at: datetime
    value: float


@dataclass(frozen=True, slots=True)
class SpanRecord:
    """Hold the span fields used for evidence and correlation."""

    trace_id: str
    span_id: str
    name: str
    starts_at: datetime
    ends_at: datetime
    error: bool
    status_message: str


@dataclass(frozen=True, slots=True)
class TraceExport:
    """Return bounded spans of one service and gaps found while reading."""

    spans: tuple[SpanRecord, ...] = ()
    gaps: tuple[str, ...] = ()


class MetricsReader(Protocol):
    """Run one allowlisted range query; raise TelemetryUnavailable on failure."""

    def query_range(
        self, query: MetricQuery, start: datetime, end: datetime, step_seconds: int
    ) -> tuple[tuple[MetricSample, ...], ...]:
        """Return every series of the query's matrix result."""
        ...


class TraceReader(Protocol):
    """Read spans of one service; raise TelemetryUnavailable on failure."""

    def read_spans(self, service: str, limits: TelemetryLimits) -> TraceExport:
        """Return bounded spans of the service and read gaps."""
        ...


def read_regular_file(path: os.PathLike[str] | str, limit: int) -> bytes:
    """Read at most limit bytes from a regular file that is not a link."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError("not a regular file")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file exceeds the size limit")
    return data


def step_for(
    start: datetime, end: datetime, scope: TelemetryScope, limits: TelemetryLimits
) -> int:
    """Choose the scope step, widened so a range never exceeds the point limit."""
    seconds = max((end - start).total_seconds(), 0.0)
    return max(scope.step_seconds, math.ceil(seconds / (limits.max_points - 1)))


def _metric_evidence(
    reader: MetricsReader,
    incident: Incident,
    scope: TelemetryScope,
    limits: TelemetryLimits,
) -> tuple[list[Observation], list[str]]:
    """Turn each allowlisted query into threshold signals, a summary and gaps."""
    source = scope.metrics_source
    start, end = incident.starts_at, incident.ends_at
    step = step_for(start, end, scope, limits)
    observations: list[Observation] = []
    gaps: list[str] = []
    for query in scope.queries:
        try:
            series = reader.query_range(query, start, end, step)
        except TelemetryUnavailable as error:
            gaps.append(str(error))
            break
        if not series:
            gaps.append(
                f"Metric {query.name} returned no data for the window; it is "
                "unavailable."
            )
            continue
        if len(series) > 1:
            gaps.append(
                f"Metric {query.name} returned {len(series)} series instead of one; "
                "it was skipped."
            )
            continue
        samples = [item for item in series[0] if start <= item.at <= end]
        finite = [item for item in samples if math.isfinite(item.value)]
        if len(finite) < len(samples):
            gaps.append(
                f"Metric {query.name} returned {len(samples) - len(finite)} "
                "non-finite values; they were skipped."
            )
        if not finite:
            gaps.append(
                f"Metric {query.name} has no usable samples in the window; it is "
                "unavailable."
            )
            continue
        runs: list[list[MetricSample]] = []
        previous_above = False
        for sample in finite:
            above = sample.value > query.threshold
            if above and not previous_above:
                runs.append([])
            if above:
                runs[-1].append(sample)
            previous_above = above
        if len(runs) > limits.max_signals_per_query:
            gaps.append(
                f"Metric {query.name} crossed its threshold {len(runs)} times; "
                f"only the first {limits.max_signals_per_query} were kept."
            )
        for index, run in enumerate(runs[: limits.max_signals_per_query], start=1):
            first, last = run[0], run[-1]
            observations.append(
                Observation(
                    f"{source}-{query.name}-{index:02d}",
                    source,
                    first.at,
                    first.at,
                    ObservationKind.METRIC_SIGNAL,
                    message=(
                        f"Metric {query.name} ({query.description}) was "
                        f"{first.value:g} at {first.at.isoformat()}, above threshold "
                        f"{query.threshold:g}; maximum {max(i.value for i in run):g}; "
                        f"last sample above it at {last.at.isoformat()}."
                    ),
                )
            )
        newest = finite[-1]
        observations.append(
            Observation(
                f"{source}-{query.name}-summary",
                source,
                newest.at,
                newest.at,
                ObservationKind.NOTE,
                message=(
                    f"Metric {query.name}: {len(finite)} samples every {step} s from "
                    f"{finite[0].at.isoformat()} to {newest.at.isoformat()}; maximum "
                    f"{max(item.value for item in finite):g}."
                ),
            )
        )
        age = (end - newest.at).total_seconds()
        if age > scope.stale_after_seconds:
            gaps.append(
                f"Metric {query.name} has no samples in the last {age:.0f} s of the "
                "window; collection may be delayed or the target stopped reporting."
            )
    return observations, gaps


def _trace_evidence(
    reader: TraceReader,
    incident: Incident,
    scope: TelemetryScope,
    limits: TelemetryLimits,
) -> tuple[list[Observation], list[str], list[SpanInterval], list[str], int]:
    """Turn error spans in the window into observations and keep span intervals."""
    source = scope.traces_source
    start, end = incident.starts_at, incident.ends_at
    try:
        export = reader.read_spans(scope.service, limits)
    except TelemetryUnavailable as error:
        return [], [str(error)], [], [], 0
    gaps = list(export.gaps)
    seen: set[tuple[str, str]] = set()
    spans: list[SpanRecord] = []
    duplicates = 0
    for span in export.spans:
        key = (span.trace_id, span.span_id)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        if span.ends_at >= start and span.starts_at <= end:
            spans.append(span)
    if duplicates:
        gaps.append(f"{duplicates} duplicate spans in the trace export were ignored.")
    errors = [span for span in spans if span.error and start <= span.ends_at <= end]
    if len(errors) > limits.max_error_spans:
        gaps.append(
            f"{len(errors)} error spans fall in the window; only the first "
            f"{limits.max_error_spans} were kept."
        )
    observations: list[Observation] = []
    truncated: list[str] = []
    redactions = 0
    for span in errors[: limits.max_error_spans]:
        message, count, cut = sanitize_text(
            f"Span {span.name} of trace {span.trace_id} ended with error status: "
            f"{span.status_message or 'no status message'}.",
            limits.max_message_chars,
        )
        observation_id = f"{source}-{span.span_id}"
        observations.append(
            Observation(
                observation_id,
                source,
                span.ends_at,
                span.ends_at,
                ObservationKind.TRACE_ERROR,
                message=message,
                trace_id=span.trace_id,
            )
        )
        redactions += count
        if cut:
            truncated.append(observation_id)
    observations.append(
        Observation(
            f"{source}-summary",
            source,
            end,
            end,
            ObservationKind.NOTE,
            message=(
                f"Trace export: {len(spans)} spans of service {scope.service} overlap "
                f"the window; {len(errors)} ended with an error status."
            ),
        )
    )
    intervals = [SpanInterval(s.trace_id, s.starts_at, s.ends_at) for s in spans]
    return observations, gaps, intervals, truncated, redactions


def collect_telemetry(
    incident: Incident,
    *,
    metrics: MetricsReader | None,
    traces: TraceReader | None,
    scope: TelemetryScope = STAGE07_TELEMETRY,
    limits: TelemetryLimits = DEFAULT_TELEMETRY_LIMITS,
) -> TelemetryEvidence:
    """Read the requested telemetry for the incident window into evidence."""
    if incident.service != scope.service:
        raise TelemetryError("Telemetry is allowlisted for another service.")
    if metrics is None and traces is None:
        raise TelemetryError("No telemetry source was requested.")
    kinds: list[str] = []
    sources: list[Source] = []
    observations: list[Observation] = []
    gaps: list[str] = []
    spans: list[SpanInterval] = []
    truncated: list[str] = []
    redactions = 0
    if metrics is not None:
        kinds.append("metrics")
        sources.append(Source(scope.metrics_source, scope.service, SourceKind.METRICS))
        found, problems = _metric_evidence(metrics, incident, scope, limits)
        observations += found
        gaps += problems
    if traces is not None:
        kinds.append("traces")
        sources.append(Source(scope.traces_source, scope.service, SourceKind.TRACES))
        found, problems, intervals, cut, count = _trace_evidence(
            traces, incident, scope, limits
        )
        observations += found
        gaps += problems
        spans += intervals
        truncated += cut
        redactions += count
    return TelemetryEvidence(
        kinds=tuple(kinds),
        sources=tuple(sources),
        observations=tuple(observations),
        gaps=tuple(gaps),
        spans=tuple(spans),
        truncated_observation_ids=tuple(sorted(truncated)),
        redaction_count=redactions,
    )
