"""Check telemetry evidence: domain rules, metric and trace mapping, merge gaps."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.application.inputs import (
    PreparedInput,
    SpanInterval,
    TelemetryEvidence,
    add_telemetry_evidence,
    trace_gaps,
)
from app.application.reporting import build_report, render_report
from app.application.snapshots import decode_input, encode_input
from app.domain import (
    Incident,
    Investigation,
    Observation,
    ObservationKind,
    Source,
    SourceKind,
    investigate,
)
from app.infrastructure.fixtures import load_fixture
from app.infrastructure.telemetry import (
    STAGE07_TELEMETRY,
    TelemetryError,
    TelemetryLimits,
    TelemetryUnavailable,
    collect_telemetry,
    step_for,
)

from telemetry_fakes import (
    FIXTURES,
    ROOT,
    START,
    StaticMetrics,
    StaticTraces,
    samples,
    span,
)

T1 = "0af7651916cd43dd8448eb211c80319c"
T2 = "5b8efff798038103d269b633813fc60c"
INCIDENT = Incident("telemetry", "training-api", START, START + timedelta(minutes=10))
LOG = Source("api-log", "training-api", SourceKind.APPLICATION_LOG)


def at(seconds: float) -> object:
    """Return a time seconds after the window start."""
    return START + timedelta(seconds=seconds)


def refusal(name: str, seconds: float, trace_id: str | None) -> Observation:
    """Build a refused-connection log line, optionally with a trace ID."""
    return Observation(
        name,
        LOG.source_id,
        START + timedelta(seconds=seconds),
        START + timedelta(seconds=seconds),
        ObservationKind.CONNECTION_REFUSED,
        trace_id=trace_id,
    )


def prepared(*observations: Observation) -> PreparedInput:
    """Wrap log observations in a prepared input for the fixed incident."""
    return PreparedInput(INCIDENT, (LOG,), observations, declared_complete=True)


# Domain


@pytest.mark.parametrize(
    "trace_id",
    ["0" * 32, "A" * 32, "abc", "g" * 32, "0af7651916cd43dd8448eb211c80319c0", 7],
)
def test_trace_ids_follow_the_w3c_format(trace_id: object) -> None:
    """Invalid, uppercase, short, long and all-zero trace IDs are rejected."""
    with pytest.raises(ValueError, match="trace_id"):
        refusal("r", 1, trace_id)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kind", "source_kind"),
    [
        (ObservationKind.METRIC_SIGNAL, SourceKind.APPLICATION_LOG),
        (ObservationKind.TRACE_ERROR, SourceKind.METRICS),
    ],
)
def test_telemetry_signals_need_their_own_source_kind(
    kind: ObservationKind, source_kind: SourceKind
) -> None:
    """A log line cannot claim to be a metric signal or a failed span."""
    source = Source("s", "training-api", source_kind)
    event = Observation("e", "s", START, START, kind)
    with pytest.raises(ValueError, match="incompatible"):
        Investigation(INCIDENT, (source,), (event,))


def test_same_trace_links_only_observations_from_different_sources() -> None:
    """A trace shared by a log and a span is a fact; one source alone is not."""
    traces = Source("training-api-traces", "training-api", SourceKind.TRACES)
    span_event = Observation(
        "span-1",
        traces.source_id,
        START + timedelta(seconds=2),
        START + timedelta(seconds=2),
        ObservationKind.TRACE_ERROR,
        trace_id=T1,
    )
    result = investigate(
        INCIDENT,
        (LOG, traces),
        (refusal("r1", 1, T1), refusal("r2", 3, T2), refusal("r3", 4, T2), span_event),
    )
    linked = [fact for fact in result.facts if fact.code == "same_trace"]
    assert len(linked) == 1
    assert [ref.observation_id for ref in linked[0].evidence] == ["r1", "span-1"]
    assert "does not establish a cause" in linked[0].statement
    assert {fact.code for fact in result.facts} >= {
        "trace_error_recorded",
        "connection_refused",
    }


def test_metric_and_trace_signals_count_as_failures_after_a_deployment() -> None:
    """A deployment followed by a metric signal is linked as a hypothesis only."""
    history = Source("lab-deployments", "training-api", SourceKind.DEPLOYMENT_HISTORY)
    metrics = Source("training-api-metrics", "training-api", SourceKind.METRICS)
    events = (
        Observation("d", "lab-deployments", START, START, ObservationKind.DEPLOYMENT),
        Observation(
            "m",
            "training-api-metrics",
            START + timedelta(seconds=30),
            START + timedelta(seconds=30),
            ObservationKind.METRIC_SIGNAL,
        ),
    )
    result = investigate(INCIDENT, (history, metrics), events)
    assert [item.code for item in result.hypotheses] == ["deployment_preceded_failure"]


def test_reports_and_snapshots_without_trace_ids_keep_their_shape() -> None:
    """Earlier fixtures render without trace_id or telemetry keys."""
    for path in sorted(ROOT.glob("fixtures/stage0[35]/*.json")):
        item = load_fixture(path)
        assert '"trace_id"' not in render_report(item)
        document = encode_input(item)
        assert '"trace_id"' not in document and '"telemetry"' not in document
        assert decode_input(document) == item


def test_trace_ids_round_trip_through_snapshots() -> None:
    """Stored inputs keep trace IDs and the telemetry list exactly."""
    item = replace(prepared(refusal("r1", 1, T1)), telemetry=("traces",))
    document = encode_input(item)
    assert decode_input(document) == item
    assert json.loads(document)["telemetry"] == ["traces"]
    assert json.loads(render_report(item))["timeline"][0]["trace_id"] == T1


@pytest.mark.parametrize(
    "telemetry", [("traces", "metrics"), ("metrics", "metrics"), ("logs",)]
)
def test_prepared_input_telemetry_list_is_sorted_and_known(
    telemetry: tuple[str, ...],
) -> None:
    """The telemetry list is canonical, so equal inputs encode equally."""
    with pytest.raises(ValueError, match="telemetry"):
        replace(prepared(), telemetry=telemetry)


# Metric mapping


def collect(**series: tuple[float, ...]) -> TelemetryEvidence:
    """Collect metrics for the incident from fixed per-query values."""
    reader = StaticMetrics(
        {name: (samples(*values),) for name, values in series.items()}
    )
    return collect_telemetry(INCIDENT, metrics=reader, traces=None)


def test_threshold_runs_become_signals_with_a_summary() -> None:
    """Each rise above the threshold is one signal; a summary covers the series."""
    evidence = collect(dependency_failures=(0, 2, 3, 0, 1), target_down=(0,) * 41)
    signals = [
        e for e in evidence.observations if e.kind is ObservationKind.METRIC_SIGNAL
    ]
    assert [(e.observation_id, e.event_time) for e in signals] == [
        ("training-api-metrics-dependency_failures-01", START + timedelta(seconds=15)),
        ("training-api-metrics-dependency_failures-02", START + timedelta(seconds=60)),
    ]
    assert "maximum 3" in signals[0].message
    assert evidence.kinds == ("metrics",)
    assert any(
        e.observation_id.endswith("target_down-summary") for e in evidence.observations
    )
    assert any(
        "dependency_failures has no samples in the last 540 s" in g
        for g in evidence.gaps
    )
    assert not any("target_down" in gap for gap in evidence.gaps)


def test_missing_multiple_and_non_finite_series_are_gaps() -> None:
    """Absent data, ambiguous series and NaN values are stated, not hidden."""
    reader = StaticMetrics(
        {
            "dependency_failures": (samples(0), samples(1)),
            "target_down": (samples(float("nan"), float("inf")),),
        }
    )
    evidence = collect_telemetry(INCIDENT, metrics=reader, traces=None)
    assert evidence.gaps == (
        "Metric dependency_failures returned 2 series instead of one; it was skipped.",
        "Metric target_down returned 2 non-finite values; they were skipped.",
        "Metric target_down has no usable samples in the window; it is unavailable.",
    )
    empty = collect_telemetry(INCIDENT, metrics=StaticMetrics({}), traces=None)
    assert all("returned no data" in gap for gap in empty.gaps)
    assert empty.observations == ()


def test_unavailable_prometheus_stops_further_queries() -> None:
    """One unavailability gap is reported and later queries are not sent."""
    reader = StaticMetrics({}, TelemetryUnavailable("Prometheus is down; fictional."))
    evidence = collect_telemetry(INCIDENT, metrics=reader, traces=None)
    assert evidence.gaps == ("Prometheus is down; fictional.",)
    assert [name for name, _ in reader.asked] == ["dependency_failures"]
    report = build_report(add_telemetry_evidence(prepared(), evidence))
    assert report["data_quality"]["status"] == "partial"  # type: ignore[index]


def test_signal_count_and_step_are_bounded() -> None:
    """Many crossings are capped and long windows widen the step."""
    limits = TelemetryLimits(max_signals_per_query=2, max_points=11)
    reader = StaticMetrics({"dependency_failures": (samples(1, 0, 1, 0, 1, 0),)})
    evidence = collect_telemetry(INCIDENT, metrics=reader, traces=None, limits=limits)
    signals = [
        e for e in evidence.observations if e.kind is ObservationKind.METRIC_SIGNAL
    ]
    assert len(signals) == 2
    assert (
        "crossed its threshold 3 times; only the first 2 were kept." in evidence.gaps[0]
    )
    assert reader.asked[0][1] == 60
    assert step_for(START, START, STAGE07_TELEMETRY, limits) == 15


def test_samples_outside_the_window_are_ignored() -> None:
    """Recorded samples before the window never become evidence."""
    early = samples(5, start=START - timedelta(minutes=5))
    reader = StaticMetrics({"dependency_failures": (early,)})
    evidence = collect_telemetry(INCIDENT, metrics=reader, traces=None)
    assert evidence.observations == ()
    assert "no usable samples" in evidence.gaps[0]


# Trace mapping


def test_error_spans_become_trace_observations_with_trace_ids() -> None:
    """Only error spans in the window are evidence; all spans stay for checks."""
    spans = (
        span(T1, "b7ad6b7169203331", 1, 1.05),
        span(T2, "eee19b7ec3c1b174", 2, 2.05, error=False),
        span(T2, "00f067aa0ba902b7", 700, 701),
    )
    evidence = collect_telemetry(INCIDENT, metrics=None, traces=StaticTraces(spans))
    errors = [e for e in evidence.observations if e.kind is ObservationKind.TRACE_ERROR]
    assert [(e.observation_id, e.trace_id) for e in errors] == [
        ("training-api-traces-b7ad6b7169203331", T1)
    ]
    assert len(evidence.spans) == 2
    assert "2 spans of service training-api overlap the window; 1 ended" in (
        evidence.observations[-1].message
    )


def test_span_text_is_redacted_and_duplicates_are_reported() -> None:
    """Status messages are untrusted text; repeated spans are counted once."""
    leaked = span(T1, "b7ad6b7169203331", 1, 2, message="token=fictional-span-secret")
    evidence = collect_telemetry(
        INCIDENT, metrics=None, traces=StaticTraces((leaked, leaked))
    )
    assert "fictional-span-secret" not in str(evidence.observations)
    assert evidence.redaction_count == 1
    assert evidence.gaps == ("1 duplicate spans in the trace export were ignored.",)


def test_trace_export_errors_and_scope_are_handled() -> None:
    """A missing export is a gap; another service or no source is refused."""
    missing = StaticTraces(error=TelemetryUnavailable("Fictional export missing."))
    evidence = collect_telemetry(INCIDENT, metrics=None, traces=missing)
    assert evidence.gaps == ("Fictional export missing.",)
    assert evidence.observations == ()
    with pytest.raises(TelemetryError, match="another service"):
        collect_telemetry(
            replace(INCIDENT, service="billing-api"), metrics=None, traces=missing
        )
    with pytest.raises(TelemetryError, match="No telemetry"):
        collect_telemetry(INCIDENT, metrics=None, traces=None)


# Merge gaps


def intervals(*items: tuple[str, float, float]) -> tuple[SpanInterval, ...]:
    """Build span intervals from seconds after the window start."""
    return tuple(
        SpanInterval(trace, START + timedelta(seconds=a), START + timedelta(seconds=b))
        for trace, a, b in items
    )


def test_untraced_failures_and_missing_traces_are_gaps() -> None:
    """Failure logs without trace IDs and traces absent from the export are listed."""
    item = prepared(refusal("r1", 1, None), refusal("r2", 2, T1), refusal("r3", 3, T2))
    gaps = trace_gaps(item, intervals((T2, 2.5, 3.5)))
    assert gaps == [
        "1 failure log observations have no trace ID and could not be linked to "
        "traces: r1.",
        "1 log observations name traces missing from the trace export; spans may be "
        "delayed, sampled or lost: r2.",
    ]


@pytest.mark.parametrize(
    ("seconds", "skewed"),
    [(2.0, False), (1.0, False), (0.999, True), (5.0, False), (6.5, True)],
)
def test_clock_skew_beyond_the_tolerance_is_a_gap(seconds: float, skewed: bool) -> None:
    """A log outside its span by more than one second suggests clock skew."""
    gaps = trace_gaps(prepared(refusal("r1", seconds, T1)), intervals((T1, 2, 5)))
    assert bool(gaps) is skewed
    if skewed:
        assert "log and trace clocks may disagree: r1 (" in gaps[0]


def test_listed_identifiers_are_capped() -> None:
    """Long lists name ten identifiers and count the rest."""
    item = prepared(*(refusal(f"r{n:02d}", n, None) for n in range(12)))
    assert trace_gaps(item, ())[0].endswith("r09 and 2 more.")


def test_merge_adds_sources_gaps_and_telemetry_once() -> None:
    """Merging records telemetry kinds and cannot be repeated."""
    evidence = collect_telemetry(
        INCIDENT,
        metrics=None,
        traces=StaticTraces((span(T1, "b7ad6b7169203331", 0.5, 1.5),)),
    )
    merged = add_telemetry_evidence(
        prepared(refusal("r1", 1, T1), refusal("r2", 2, None)), evidence
    )
    assert merged.telemetry == ("traces",)
    assert any("no trace ID" in gap for gap in merged.gaps)
    facts = {
        fact.code
        for fact in investigate(
            merged.incident, merged.sources, merged.observations
        ).facts
    }
    assert "same_trace" in facts
    with pytest.raises(ValueError, match="already"):
        add_telemetry_evidence(merged, evidence)


def test_metrics_only_merge_does_not_check_traces() -> None:
    """Trace correlation gaps appear only when traces were read."""
    evidence = collect(dependency_failures=(0,) * 41, target_down=(0,) * 41)
    merged = add_telemetry_evidence(prepared(refusal("r1", 1, None)), evidence)
    assert merged.gaps == ()
    assert merged.telemetry == ("metrics",)


def test_offline_fixture_tells_one_consistent_story() -> None:
    """The Stage 07 fixture links logs, spans and metrics with one visible gap."""
    from app.infrastructure.otlp_traces import OtlpFileReader
    from app.infrastructure.prometheus import RecordedPrometheus

    limits = TelemetryLimits()
    base = load_fixture(FIXTURES / "incident.json")
    evidence = collect_telemetry(
        base.incident,
        metrics=RecordedPrometheus(FIXTURES / "metrics.json", limits),
        traces=OtlpFileReader(FIXTURES / "traces.jsonl"),
    )
    report = json.loads(render_report(add_telemetry_evidence(base, evidence)))
    codes = [fact["code"] for fact in report["facts"]]
    assert codes.count("same_trace") == 3
    assert codes.count("metric_threshold_exceeded") == 2
    assert codes.count("trace_error_recorded") == 3
    assert report["data_quality"]["gaps"] == [
        "Metric dependency_failures has no samples in the last 525 s of the window; "
        "collection may be delayed or the target stopped reporting."
    ]
    assert any(
        "correlated, not causally linked" in item for item in report["limitations"]
    )


def test_docker_log_lines_carry_only_valid_trace_ids() -> None:
    """A structured log line's trace_id is kept only in W3C form."""
    from app.infrastructure.docker_lab import _signal

    valid = json.dumps({"event": "connection_refused", "trace_id": T1})
    assert _signal(valid) == (ObservationKind.CONNECTION_REFUSED, T1)
    for trace in ("0" * 32, T1.upper(), 5, "not-a-trace"):
        line = json.dumps({"event": "startup", "trace_id": trace})
        assert _signal(line) == (ObservationKind.NOTE, None)
    assert _signal(f"text trace_id={T1}") == (ObservationKind.NOTE, None)
