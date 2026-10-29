"""Check the Prometheus client, recorded metrics and the OTLP trace reader."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.infrastructure.otlp_traces import OtlpFileReader
from app.infrastructure.prometheus import (
    PrometheusClient,
    RecordedPrometheus,
    parse_loopback_url,
    parse_matrix,
)
from app.infrastructure.telemetry import (
    STAGE07_TELEMETRY,
    MetricQuery,
    TelemetryError,
    TelemetryLimits,
    TelemetryUnavailable,
)

from telemetry_fakes import FIXTURES, START, fake_prometheus, matrix

LIMITS = TelemetryLimits()
QUERY = STAGE07_TELEMETRY.queries[0]
ALLOWED = frozenset(query.expression for query in STAGE07_TELEMETRY.queries)
END = START + timedelta(minutes=10)
T1 = "0af7651916cd43dd8448eb211c80319c"


# Prometheus URL and client


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:19090", "http://localhost:9090/", "http://[::1]:9090"],
)
def test_loopback_urls_are_accepted(url: str) -> None:
    """Only a loopback host with an explicit port is accepted."""
    host, port = parse_loopback_url(url)
    assert host in {"127.0.0.1", "localhost", "::1"} and port > 0


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:9090",
        "http://prometheus.example.invalid:9090",
        "http://10.0.0.5:9090",
        "http://127.0.0.1",
        "http://user:secret@127.0.0.1:9090",
        "http://127.0.0.1:9090/api/v1/admin",
        "http://127.0.0.1:9090/?query=up",
        "http://127.0.0.1:99999",
        "file:///etc/passwd",
    ],
)
def test_other_urls_are_refused_without_echo(url: str) -> None:
    """Remote, TLS, credentialed, path-bearing or invalid URLs are refused."""
    with pytest.raises(TelemetryError) as error:
        parse_loopback_url(url)
    assert "secret" not in str(error.value) and "example" not in str(error.value)


def test_client_sends_only_the_allowlisted_get_range_query() -> None:
    """One GET to query_range with the fixed expression and window."""
    with fake_prometheus() as server:
        server.respond(QUERY.expression, matrix([(START.timestamp(), "1")]))
        client = PrometheusClient(server.url, ALLOWED, LIMITS)
        series = client.query_range(QUERY, START, END, 15)
    assert series[0][0].value == 1.0
    assert len(server.requests) == 1
    method, path, query = server.requests[0]
    assert (method, path) == ("GET", "/api/v1/query_range")
    assert query == {
        "query": [QUERY.expression],
        "start": [f"{START.timestamp():.3f}"],
        "end": [f"{END.timestamp():.3f}"],
        "step": ["15s"],
    }


def test_query_outside_the_allowlist_is_refused_before_connecting() -> None:
    """A changed expression never reaches Prometheus."""
    with fake_prometheus() as server:
        client = PrometheusClient(server.url, ALLOWED, LIMITS)
        rogue = MetricQuery("rogue", "count({__name__=~'.+'})", 0, "everything")
        with pytest.raises(TelemetryError, match="not allowlisted"):
            client.query_range(rogue, START, END, 15)
    assert server.requests == []


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (500, b"fictional-private-error", "HTTP status 500"),
        (200, b"not json", "not valid JSON"),
        (200, json.dumps({"status": "error"}).encode(), "unsuccessful"),
        (
            200,
            json.dumps(
                {"status": "success", "data": {"resultType": "vector"}}
            ).encode(),
            "not a range matrix",
        ),
        (200, b'{"status": "success", "status": "success"}', "not valid JSON"),
        (200, b"x" * 2_000, "size limit"),
    ],
)
def test_bad_responses_make_metrics_unavailable(
    status: int, body: bytes, reason: str
) -> None:
    """Errors, malformed JSON and oversize bodies become safe gap text."""
    with fake_prometheus() as server:
        server.respond(QUERY.expression, body, status)
        client = PrometheusClient(
            server.url, ALLOWED, TelemetryLimits(max_response_bytes=1_000)
        )
        with pytest.raises(TelemetryUnavailable) as error:
            client.query_range(QUERY, START, END, 15)
    assert reason in str(error.value)
    assert "fictional-private-error" not in str(error.value)


def test_slow_and_unreachable_prometheus_are_unavailable() -> None:
    """A body slower than the deadline and a closed port both become gaps."""
    with fake_prometheus() as server:
        server.respond(QUERY.expression, matrix([(START.timestamp(), "1")] * 200))
        server.delay = 1.5
        client = PrometheusClient(
            server.url, ALLOWED, TelemetryLimits(timeout_seconds=1)
        )
        with pytest.raises(TelemetryUnavailable, match="time limit|unreachable"):
            client.query_range(QUERY, START, END, 15)
        closed = server.url
    with pytest.raises(TelemetryUnavailable, match="unreachable"):
        PrometheusClient(closed, ALLOWED, LIMITS).query_range(QUERY, START, END, 15)


@pytest.mark.parametrize(
    "values",
    [
        [[1, 2]],
        [["1", "2"]],
        [[True, "1"]],
        [[1e30, "1"]],
        [[1, "not-a-number"]],
        [[1, "2", "3"]],
    ],
)
def test_malformed_samples_are_refused(values: list[object]) -> None:
    """Sample timestamps must be numbers and values numeric strings."""
    payload = {
        "status": "success",
        "data": {"resultType": "matrix", "result": [{"values": values}]},
    }
    with pytest.raises(TelemetryUnavailable, match="malformed"):
        parse_matrix(payload, LIMITS)


def test_series_point_limit_and_ordering() -> None:
    """Oversized series are refused; samples are returned in time order."""
    with pytest.raises(TelemetryUnavailable, match="oversized"):
        parse_matrix(matrix([(1, "1")] * 3), TelemetryLimits(max_points=2))
    series = parse_matrix(matrix([(20, "2"), (10, "1")]), LIMITS)
    assert [sample.value for sample in series[0]] == [1.0, 2.0]


def test_recorded_metrics_answer_by_query_name(tmp_path: Path) -> None:
    """Recordings return series for known names and nothing for others."""
    reader = RecordedPrometheus(FIXTURES / "metrics.json", LIMITS)
    assert len(reader.query_range(QUERY, START, END, 15)[0]) == 6
    unknown = MetricQuery("unknown", "x", 0, "x")
    assert reader.query_range(unknown, START, END, 15) == ()
    for content in (b"[]", b"{}", b"not json"):
        path = tmp_path / "metrics.json"
        path.write_bytes(content)
        with pytest.raises(TelemetryUnavailable):
            RecordedPrometheus(path, LIMITS).query_range(QUERY, START, END, 15)
    with pytest.raises(TelemetryUnavailable):
        RecordedPrometheus(tmp_path / "missing.json", LIMITS).query_range(
            QUERY, START, END, 15
        )


# OTLP trace export


def otlp(*spans: dict[str, object], service: str = "training-api") -> str:
    """Build one OTLP JSON export line for the given spans."""
    return json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": service}}
                        ]
                    },
                    "scopeSpans": [{"spans": list(spans)}],
                }
            ]
        }
    )


def raw_span(**changes: object) -> dict[str, object]:
    """Build a valid raw span with optional field changes."""
    item: dict[str, object] = {
        "traceId": T1,
        "spanId": "b7ad6b7169203331",
        "name": "connect dependency",
        "startTimeUnixNano": "1793271660000000000",
        "endTimeUnixNano": "1793271660045000000",
        "status": {"code": 2, "message": "connection refused"},
    }
    item.update(changes)
    return item


def read(tmp_path: Path, *lines: str, limits: TelemetryLimits = LIMITS) -> object:
    """Write lines to an export file and read the service's spans."""
    path = tmp_path / "traces.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return OtlpFileReader(path).read_spans("training-api", limits)


def test_fixture_export_is_read() -> None:
    """The committed fictional export has one OK and three error spans."""
    export = OtlpFileReader(FIXTURES / "traces.jsonl").read_spans(
        "training-api", LIMITS
    )
    assert [span.error for span in export.spans] == [False, True, True, True]
    assert export.gaps == ()


def test_spans_are_normalized(tmp_path: Path) -> None:
    """Uppercase IDs are lowered, integer nanoseconds and string codes accepted."""
    line = otlp(
        raw_span(
            traceId=T1.upper(),
            startTimeUnixNano=1793271660000000000,
            status={"code": "STATUS_CODE_ERROR"},
        )
    )
    export = read(tmp_path, line)
    (span,) = export.spans  # type: ignore[attr-defined]
    assert span.trace_id == T1 and span.error is True
    assert span.ends_at - span.starts_at == timedelta(milliseconds=45)


def test_other_services_and_malformed_records_are_skipped(tmp_path: Path) -> None:
    """Only the allowlisted service is read; bad lines and spans are counted."""
    export = read(
        tmp_path,
        otlp(raw_span(), service="billing-api"),
        "not json",
        otlp(raw_span(traceId="0" * 32)),
        otlp(raw_span(spanId="xyz")),
        otlp(raw_span(endTimeUnixNano="1")),
        otlp(raw_span(startTimeUnixNano=-5)),
        otlp(raw_span(status=[])),
        otlp(raw_span()),
    )
    assert len(export.spans) == 1  # type: ignore[attr-defined]
    assert export.gaps == (  # type: ignore[attr-defined]
        "1 trace export lines were not valid OTLP JSON; they were skipped.",
        "5 spans or resources were malformed; they were skipped.",
    )


def test_export_limits_are_enforced(tmp_path: Path) -> None:
    """Line, span and byte limits are reported or refuse the file."""
    many = read(
        tmp_path,
        *(otlp(raw_span(spanId=f"{n + 1:016x}")) for n in range(4)),
        limits=TelemetryLimits(max_trace_lines=3, max_spans=2),
    )
    assert len(many.spans) == 2  # type: ignore[attr-defined]
    assert len(many.gaps) == 2  # type: ignore[attr-defined]
    with pytest.raises(TelemetryUnavailable, match="size limit"):
        read(tmp_path, otlp(raw_span()), limits=TelemetryLimits(max_trace_bytes=10))


def test_links_missing_files_and_bad_encoding_are_unavailable(tmp_path: Path) -> None:
    """The export must be a readable regular UTF-8 file, not a link."""
    target = tmp_path / "real.jsonl"
    target.write_text(otlp(raw_span()))
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    binary = tmp_path / "binary.jsonl"
    binary.write_bytes(b"\xff\xfe")
    for path in (link, binary, tmp_path / "missing.jsonl", tmp_path):
        with pytest.raises(TelemetryUnavailable):
            OtlpFileReader(path).read_spans("training-api", LIMITS)
