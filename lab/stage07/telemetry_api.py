"""Fictional training API for the Stage 07 lab: logs, a metric and spans.

Each attempt to reach a dependency that is not listening produces three
records of the same failure: a structured log line with a trace ID, an
increment of a Prometheus counter, and an OTLP span with an error status.
After the attempts the process exits with code 3. Standard library only.
"""

import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TextIO

SERVICE = "training-api"
EXIT_DEPENDENCY_REFUSED = 3


class Counter:
    """Count failed dependency connections for the metrics endpoint."""

    def __init__(self) -> None:
        """Start at zero with a lock for the metrics thread."""
        self._lock = threading.Lock()
        self._value = 0

    def increment(self) -> None:
        """Add one failure."""
        with self._lock:
            self._value += 1

    @property
    def value(self) -> int:
        """Return the current count."""
        with self._lock:
            return self._value


def render_metrics(failures: int) -> bytes:
    """Render the counter in the Prometheus text exposition format."""
    return (
        "# HELP training_api_dependency_failures_total "
        "Fictional failed dependency connections.\n"
        "# TYPE training_api_dependency_failures_total counter\n"
        f"training_api_dependency_failures_total {failures}\n"
    ).encode("ascii")


def metrics_handler(counter: Counter) -> type[BaseHTTPRequestHandler]:
    """Build a handler that serves only GET /metrics."""

    class Handler(BaseHTTPRequestHandler):
        """Serve the metrics page and nothing else."""

        def do_GET(self) -> None:  # noqa: N802 - name required by http.server
            """Return the counter, or 404 for any other path."""
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = render_metrics(counter.value)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Keep scrape requests out of the application log."""

    return Handler


def log_event(event: str, stream: TextIO = sys.stdout, **fields: object) -> None:
    """Write one structured JSON log line."""
    print(
        json.dumps({"event": event, **fields}, sort_keys=True), file=stream, flush=True
    )


def connect(host: str, port: int) -> str | None:
    """Try the dependency once; return an error description or None."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return None
    except ConnectionRefusedError:
        return "connection refused"
    except OSError:
        return "connection failed"


def span_payload(
    trace_id: str, span_id: str, start_ns: int, end_ns: int, error: str | None
) -> dict[str, object]:
    """Build one OTLP/HTTP JSON export request for a single span."""
    status: dict[str, object] = (
        {"code": 2, "message": error} if error is not None else {"code": 1}
    )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": SERVICE}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "training-api-lab"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "connect dependency",
                                "kind": 3,
                                "startTimeUnixNano": str(start_ns),
                                "endTimeUnixNano": str(end_ns),
                                "status": status,
                            }
                        ],
                    }
                ],
            }
        ]
    }


def export_span(endpoint: str, payload: dict[str, object]) -> bool:
    """Send one span to the collector; report failure instead of raising."""
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            status: int = response.status
            return 200 <= status < 300
    except (OSError, urllib.error.URLError):
        return False


def main() -> int:
    """Serve metrics, make the configured attempts, then exit with code 3."""
    host = os.environ.get("DEPENDENCY_HOST", "127.0.0.1")
    port = int(os.environ.get("DEPENDENCY_PORT", "5432"))
    endpoint = os.environ.get("OTLP_TRACES_ENDPOINT", "")
    attempts = int(os.environ.get("ATTEMPTS", "6"))
    interval = float(os.environ.get("INTERVAL_SECONDS", "5"))
    counter = Counter()
    server = ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("METRICS_PORT", "9464"))),
        metrics_handler(counter),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log_event("startup", message="Fictional training-api with telemetry starting.")
    for _ in range(attempts):
        trace_id, span_id = secrets.token_hex(16), secrets.token_hex(8)
        start_ns = time.time_ns()
        error = connect(host, port)
        end_ns = time.time_ns()
        if error is not None:
            counter.increment()
            log_event(
                "connection_refused",
                sys.stderr,
                trace_id=trace_id,
                span_id=span_id,
                message=f"Fictional dependency {error}.",
            )
        if endpoint and not export_span(
            endpoint, span_payload(trace_id, span_id, start_ns, end_ns, error)
        ):
            log_event("trace_export_failed", sys.stderr, trace_id=trace_id)
        time.sleep(interval)
    log_event("exiting", message="Fictional training-api stopping after failures.")
    server.shutdown()
    return EXIT_DEPENDENCY_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
