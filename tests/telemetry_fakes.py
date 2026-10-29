"""Offline telemetry doubles: a fake Prometheus server and in-memory readers."""

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.infrastructure.telemetry import (
    MetricQuery,
    MetricSample,
    SpanRecord,
    TelemetryLimits,
    TelemetryUnavailable,
    TraceExport,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures/stage07"
START = datetime(2026, 10, 29, 11, 0, tzinfo=UTC)


def matrix(*series: list[tuple[float, str]]) -> dict[str, object]:
    """Build a Prometheus range-query response with the given series."""
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {"metric": {"job": "training-api"}, "values": [list(p) for p in s]}
                for s in series
            ],
        },
    }


class FakePrometheus:
    """Answer range queries on 127.0.0.1 and record every request line."""

    def __init__(self) -> None:
        """Start a threaded HTTP server on a free loopback port."""
        self.requests: list[tuple[str, str, dict[str, list[str]]]] = []
        self.responses: dict[str, tuple[int, bytes]] = {}
        self.delay = 0.0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            """Record the request and return the configured response."""

            def _answer(self) -> None:
                """Serve the response configured for the query expression."""
                parts = urlsplit(self.path)
                query = parse_qs(parts.query)
                fake.requests.append((self.command, parts.path, query))
                expression = query.get("query", [""])[0]
                status, body = fake.responses.get(
                    expression, (200, json.dumps(matrix()).encode())
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if fake.delay:
                    half = len(body) // 2
                    self.wfile.write(body[:half])
                    self.wfile.flush()
                    time.sleep(fake.delay)
                    self.wfile.write(body[half:])
                else:
                    self.wfile.write(body)

            do_GET = _answer  # noqa: N815 - names required by http.server
            do_POST = _answer  # noqa: N815

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                """Keep test output quiet."""

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, args=(0.05,), daemon=True
        )
        self.thread.start()

    def respond(self, expression: str, payload: object, status: int = 200) -> None:
        """Configure the JSON answer for one query expression."""
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.responses[expression] = (status, body)

    def close(self) -> None:
        """Stop the server."""
        self.server.shutdown()
        self.server.server_close()


@contextmanager
def fake_prometheus() -> Iterator[FakePrometheus]:
    """Run a fake Prometheus for the duration of a block."""
    server = FakePrometheus()
    try:
        yield server
    finally:
        server.close()


class StaticMetrics:
    """Return fixed series per query name, or raise a fixed error."""

    def __init__(
        self,
        series: dict[str, tuple[tuple[MetricSample, ...], ...]],
        error: TelemetryUnavailable | None = None,
    ) -> None:
        """Remember the answers and record the queries asked."""
        self.series = series
        self.error = error
        self.asked: list[tuple[str, int]] = []

    def query_range(
        self, query: MetricQuery, start: datetime, end: datetime, step_seconds: int
    ) -> tuple[tuple[MetricSample, ...], ...]:
        """Return the configured series for the query name."""
        self.asked.append((query.name, step_seconds))
        if self.error is not None:
            raise self.error
        return self.series.get(query.name, ())


class StaticTraces:
    """Return a fixed trace export, or raise a fixed error."""

    def __init__(
        self, spans: tuple[SpanRecord, ...] = (), error: Exception | None = None
    ) -> None:
        """Remember the export to return."""
        self.spans = spans
        self.error = error

    def read_spans(self, service: str, limits: TelemetryLimits) -> TraceExport:
        """Return the configured spans."""
        if self.error is not None:
            raise self.error
        return TraceExport(self.spans)


def samples(
    *values: float, start: datetime = START, step: int = 15
) -> tuple[MetricSample, ...]:
    """Build evenly spaced samples starting at the window start."""
    return tuple(
        MetricSample(start + timedelta(seconds=step * index), value)
        for index, value in enumerate(values)
    )


def span(
    trace_id: str,
    span_id: str,
    starts: float,
    ends: float,
    *,
    error: bool = True,
    message: str = "connection refused",
) -> SpanRecord:
    """Build a span with times given in seconds after the window start."""
    return SpanRecord(
        trace_id,
        span_id,
        "connect dependency",
        START + timedelta(seconds=starts),
        START + timedelta(seconds=ends),
        error,
        message,
    )
