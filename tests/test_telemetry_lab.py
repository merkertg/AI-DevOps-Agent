"""Run the Stage 07 lab process without Docker and check the compose files agree."""

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.application.inputs import PreparedInput, SpanInterval, trace_gaps
from app.domain import Incident, Observation, ObservationKind, Source, SourceKind
from app.infrastructure.docker_lab import STAGE07_LAB
from app.infrastructure.otlp_traces import OtlpFileReader
from app.infrastructure.telemetry import STAGE07_TELEMETRY, TelemetryLimits

from telemetry_fakes import ROOT

LAB = ROOT / "lab/stage07"
SCRIPT = LAB / "telemetry_api.py"


def free_port() -> int:
    """Return a loopback port that nothing listens on right now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Collector:
    """Accept OTLP/HTTP posts on loopback and keep their bodies."""

    def __init__(self) -> None:
        """Start the fake collector on a free port."""
        self.bodies: list[bytes] = []
        collector = self

        class Handler(BaseHTTPRequestHandler):
            """Store each posted body and answer 200."""

            def do_POST(self) -> None:  # noqa: N802 - name required by http.server
                """Record one export request."""
                length = int(self.headers.get("Content-Length", "0"))
                collector.bodies.append(self.rfile.read(length))
                self.send_response(200)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                """Keep test output quiet."""

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}/v1/traces"
        threading.Thread(
            target=self.server.serve_forever, args=(0.05,), daemon=True
        ).start()

    def close(self) -> None:
        """Stop the collector."""
        self.server.shutdown()
        self.server.server_close()


def test_lab_process_emits_matching_logs_spans_and_exit_code(tmp_path: Path) -> None:
    """Each refusal has one log line and one error span with the same trace ID."""
    collector = Collector()
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT)],
            env={
                "PATH": os.environ.get("PATH", ""),
                "DEPENDENCY_HOST": "127.0.0.1",
                "DEPENDENCY_PORT": str(free_port()),
                "OTLP_TRACES_ENDPOINT": collector.endpoint,
                "METRICS_PORT": str(free_port()),
                "ATTEMPTS": "2",
                "INTERVAL_SECONDS": "0.05",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        collector.close()
    assert result.returncode == 3
    refusals = [json.loads(line) for line in result.stderr.splitlines()]
    assert [line["event"] for line in refusals] == ["connection_refused"] * 2
    export = tmp_path / "traces.jsonl"
    export.write_bytes(b"\n".join(collector.bodies) + b"\n")
    spans = OtlpFileReader(export).read_spans("training-api", TelemetryLimits()).spans
    assert [span.trace_id for span in spans] == [line["trace_id"] for line in refusals]
    assert all(span.error for span in spans)
    now = datetime.now(UTC)
    source = Source("api-log", "training-api", SourceKind.APPLICATION_LOG)
    logs = tuple(
        Observation(
            f"log-{index}",
            "api-log",
            span.starts_at,
            span.starts_at,
            ObservationKind.CONNECTION_REFUSED,
            trace_id=span.trace_id,
        )
        for index, span in enumerate(spans)
    )
    incident = Incident("lab", "training-api", spans[0].starts_at, now)
    prepared = PreparedInput(incident, (source,), logs)
    intervals = tuple(SpanInterval(s.trace_id, s.starts_at, s.ends_at) for s in spans)
    assert trace_gaps(prepared, intervals) == []


def test_metrics_page_uses_the_queried_counter() -> None:
    """The exposed counter is the one the allowlisted query reads."""
    spec = importlib.util.spec_from_file_location("stage07_telemetry_api", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = module.render_metrics(4).decode()
    assert "training_api_dependency_failures_total 4\n" in text
    assert (
        "training_api_dependency_failures_total"
        in STAGE07_TELEMETRY.queries[0].expression
    )


def test_compose_matches_the_allowlists_and_publishes_only_loopback() -> None:
    """Names, labels, ports, images and paths agree with the code."""
    compose = (LAB / "compose.yaml").read_text()
    assert f"container_name: {STAGE07_LAB.containers[0]}" in compose
    assert f"ai-devops-agent.lab: {STAGE07_LAB.lab}" in compose
    assert "docker.sock" not in compose
    for image in (
        "python:3.12.15-slim",
        "otel/opentelemetry-collector-contrib:0.161.0",
        "prom/prometheus:v3.15.0",
    ):
        assert f"image: {image}" in compose
    assert "internal: true" in compose
    assert compose.count('"127.0.0.1:19090:9090"') == 1
    assert compose.count("ports:") == 1
    assert "OTLP_TRACES_ENDPOINT: http://otel-collector:4318/v1/traces" in compose
    assert 'METRICS_PORT: "9464"' in compose
    assert "../../var/stage07/telemetry:/export" in compose
    assert "--web.enable-admin-api" not in compose
    assert "--web.enable-lifecycle" not in compose
    prometheus = (LAB / "prometheus.yml").read_text()
    assert "job_name: training-api" in prometheus
    assert 'targets: ["training-api:9464"]' in prometheus
    assert all('job="training-api"' in q.expression for q in STAGE07_TELEMETRY.queries)
    collector = (LAB / "otel-collector.yaml").read_text()
    assert "endpoint: 0.0.0.0:4318" in collector
    assert "path: /export/traces.jsonl" in collector
