"""Run the CLIs with metrics and traces: offline files, fake Prometheus and Docker."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent.__main__ import main as agent_main
from app.cli import main as cli_main
from app.collect import main as collect_main
from app.infrastructure.telemetry import STAGE07_TELEMETRY
from app.investigations import main as investigations_main

from docker_fake import FakeDocker, frame, inspect_document, stamp
from telemetry_fakes import FIXTURES, fake_prometheus, matrix

INCIDENT = str(FIXTURES / "incident.json")
TELEMETRY = [
    "--metrics-file",
    str(FIXTURES / "metrics.json"),
    "--traces",
    str(FIXTURES / "traces.jsonl"),
]
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def run_cli(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    """Run the fixture CLI with a fixed clock."""
    capsys.readouterr()
    code = cli_main(arguments, clock=lambda: NOW)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_fixture_with_recorded_telemetry_links_all_three_signals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Logs, spans and metrics of the fixture agree and the report says so."""
    code, out, err = run_cli([INCIDENT, *TELEMETRY], capsys)
    assert (code, err) == (0, "")
    report = json.loads(out)
    codes = [fact["code"] for fact in report["facts"]]
    assert codes.count("same_trace") == 3
    assert "metric_threshold_exceeded" in codes and "trace_error_recorded" in codes
    assert report["data_quality"]["status"] == "partial"
    assert run_cli([INCIDENT, *TELEMETRY], capsys)[1] == out


def test_saved_telemetry_investigation_verifies_and_feeds_the_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stored telemetry evidence reproduces and the agent cites trace facts."""
    store = tmp_path / "store.sqlite3"
    code, out, _ = run_cli([INCIDENT, *TELEMETRY, "--store", str(store)], capsys)
    assert code == 0
    identifier = json.loads(out)["investigation_id"]
    assert investigations_main(["verify", identifier, "--store", str(store)]) == 0
    capsys.readouterr()
    assert agent_main([identifier, "--store", str(store)]) == 0
    report = json.loads(capsys.readouterr().out)
    statements = [item["statement"] for item in report["hypotheses"]]
    assert any("same requests agree" in text for text in statements)
    assert report["next_check"]["kind"] == "review_dependency_endpoint"
    with sqlite3.connect(store) as connection:
        document = connection.execute(
            "SELECT input_document FROM investigations"
        ).fetchone()[0]
    assert json.loads(document)["telemetry"] == ["metrics", "traces"]


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:9090",
        "https://127.0.0.1:9090",
        "http://fictional-private-host:1",
    ],
)
def test_non_loopback_prometheus_is_refused(
    url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Remote Prometheus URLs exit 2 without repeating the URL."""
    code, out, err = run_cli([INCIDENT, "--prometheus", url], capsys)
    assert (code, out) == (2, "")
    assert "loopback" in err and "fictional-private-host" not in err


def test_metric_inputs_are_mutually_exclusive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Live and recorded metrics cannot be mixed in one run."""
    with pytest.raises(SystemExit) as error:
        cli_main([INCIDENT, "--prometheus", "http://127.0.0.1:1", *TELEMETRY])
    assert error.value.code == 2


def test_missing_trace_export_is_a_gap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absent export leaves a partial report, not a failure."""
    code, out, _ = run_cli([INCIDENT, "--traces", str(tmp_path / "none.jsonl")], capsys)
    report = json.loads(out)
    assert code == 0
    assert any(
        "trace export was not found" in gap for gap in report["data_quality"]["gaps"]
    )
    assert (
        "training-api-traces" in report["data_quality"]["sources_without_observations"]
    )


def test_other_service_fixture_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Telemetry is allowlisted for training-api only."""
    data = json.loads(Path(INCIDENT).read_text())
    data["incident"]["service"] = "billing-api"
    for source in data["sources"]:
        source["service"] = "billing-api"
    path = tmp_path / "other.json"
    path.write_text(json.dumps(data))
    code, _, err = run_cli([str(path), *TELEMETRY], capsys)
    assert code == 2 and "another service" in err


def test_stage07_lab_collection_with_live_style_telemetry(
    fake_docker: FakeDocker, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Docker logs with trace IDs, fake Prometheus and an export are correlated."""
    trace = "5b8efff798038103d269b633813fc60c"
    start = NOW - timedelta(minutes=2)
    line = json.dumps({"event": "connection_refused", "trace_id": trace})
    fake_docker.container(
        inspect_document(
            name="aidevops-lab07-api",
            label="stage07",
            finished_at=stamp(start + timedelta(seconds=5)),
        ),
        frame(2, f"{stamp(start + timedelta(milliseconds=40))} {line}\n"),
    )
    export = tmp_path / "traces.jsonl"
    nanos = int(start.timestamp()) * 1_000_000_000
    export.write_text(
        json.dumps(
            {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "service.name",
                                    "value": {"stringValue": "training-api"},
                                }
                            ]
                        },
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": trace,
                                        "spanId": "eee19b7ec3c1b174",
                                        "name": "connect dependency",
                                        "startTimeUnixNano": str(nanos),
                                        "endTimeUnixNano": str(nanos + 45_000_000),
                                        "status": {"code": 2, "message": "refused"},
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        )
        + "\n"
    )
    with fake_prometheus() as server:
        point = (start.timestamp(), "1")
        for query in STAGE07_TELEMETRY.queries:
            server.respond(query.expression, matrix([point, (NOW.timestamp(), "1")]))
        capsys.readouterr()
        code = collect_main(
            [
                "--lab",
                "stage07",
                "--docker-socket",
                fake_docker.socket_path,
                "--prometheus",
                server.url,
                "--traces",
                str(export),
            ],
            environ={},
            clock=lambda: NOW,
        )
        report = json.loads(capsys.readouterr().out)
    assert code == 0
    codes = [fact["code"] for fact in report["facts"]]
    assert "same_trace" in codes and codes.count("metric_threshold_exceeded") == 2
    assert {method for method, _, _ in server.requests} == {"GET"}
    assert {path for _, path, _ in server.requests} == {"/api/v1/query_range"}
    assert report["incident"]["incident_id"].startswith("stage07-")
