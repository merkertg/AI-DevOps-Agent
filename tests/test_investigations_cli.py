"""Verify saved investigations through actual CLI processes and frozen evidence."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.collect import main as collect_main

from docker_fake import FakeDocker, frame, inspect_document, stamp
from git_lab import record_deployment, tree_state

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/stage05/after_deployment.json"
NOW = datetime(2026, 10, 29, 10, 30, tzinfo=UTC)


def run_cli(module: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a real CLI with captured streams and a finite process deadline."""
    return subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_saved_report_survives_reopen_repeat_and_new_deployment(
    lab_repository: Path,
    tmp_path: Path,
) -> None:
    """A new collection cannot overwrite the old report or mutate its Git source."""
    database = tmp_path / "records.sqlite3"
    arguments = [
        str(FIXTURE),
        "--repository",
        str(lab_repository),
        "--store",
        str(database),
    ]
    repository_before = tree_state(lab_repository)
    first = run_cli("app.cli", *arguments)
    assert first.returncode == 0, first.stderr
    receipt = json.loads(first.stdout)
    assert receipt["created"] is True
    identity = receipt["investigation_id"]
    shown = run_cli("app.investigations", "show", identity, "--store", str(database))
    assert shown.returncode == 0, shown.stderr
    report = json.loads(shown.stdout)
    assert len(report["timeline"]) == 5
    assert len(report["facts"]) == len(report["hypotheses"]) == 3
    assert report["timeline"][0]["kind"] == "deployment"
    assert report["timeline"][0]["event_time"] == "2026-10-29T10:20:00+00:00"
    assert hashlib.sha256(shown.stdout.encode()).hexdigest() == receipt["report_sha256"]
    assert tree_state(lab_repository) == repository_before

    repeated = run_cli("app.cli", *arguments)
    assert repeated.returncode == 0, repeated.stderr
    second_receipt = json.loads(repeated.stdout)
    assert second_receipt.pop("created") is False
    original_receipt = dict(receipt)
    original_receipt.pop("created")
    assert second_receipt == original_receipt

    store_before = (database.read_bytes(), database.stat().st_mtime_ns)
    verified = run_cli(
        "app.investigations", "verify", identity, "--store", str(database)
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["status"] == "verified"
    listing = run_cli(
        "app.investigations", "list", "--store", str(database), "--limit", "1"
    )
    assert listing.returncode == 0
    assert json.loads(listing.stdout)["total"] == 1
    assert (database.read_bytes(), database.stat().st_mtime_ns) == store_before
    assert tree_state(lab_repository) == repository_before

    record_deployment(lab_repository, datetime(2026, 10, 29, 10, 24, tzinfo=UTC))
    new_repository_state = tree_state(lab_repository)
    new_run = run_cli("app.cli", *arguments)
    assert new_run.returncode == 0, new_run.stderr
    new_receipt = json.loads(new_run.stdout)
    assert new_receipt["created"] is True
    assert new_receipt["investigation_id"] != identity
    old_report = run_cli(
        "app.investigations", "show", identity, "--store", str(database)
    )
    assert old_report.stdout == shown.stdout
    assert tree_state(lab_repository) == new_repository_state
    assert (
        json.loads(
            run_cli("app.investigations", "list", "--store", str(database)).stdout
        )["total"]
        == 2
    )


def test_missing_history_is_saved_as_a_gap_and_remains_verifiable(
    tmp_path: Path,
) -> None:
    """Unavailable Git remains visible and does not prevent a partial saved report."""
    database = tmp_path / "partial.sqlite3"
    saved = run_cli(
        "app.cli",
        str(FIXTURE),
        "--repository",
        str(tmp_path / "missing-private-marker"),
        "--store",
        str(database),
    )
    assert saved.returncode == 0, saved.stderr
    receipt = json.loads(saved.stdout)
    assert receipt["data_quality"] == "partial"
    shown = run_cli(
        "app.investigations",
        "show",
        receipt["investigation_id"],
        "--store",
        str(database),
    )
    report = json.loads(shown.stdout)
    assert report["data_quality"]["gaps"]
    assert "missing-private-marker" not in shown.stdout + saved.stderr
    assert not any(event["kind"] == "deployment" for event in report["timeline"])
    verified = run_cli(
        "app.investigations",
        "verify",
        receipt["investigation_id"],
        "--store",
        str(database),
    )
    assert verified.returncode == 0


def test_stored_fixture_can_be_inspected_without_site_packages(tmp_path: Path) -> None:
    """Saved reports and their verification need neither GitPython nor web settings."""
    database = tmp_path / "offline.sqlite3"
    saved = run_cli("app.cli", str(FIXTURE), "--store", str(database))
    assert saved.returncode == 0
    identity = json.loads(saved.stdout)["investigation_id"]
    program = (
        f"import sys, runpy; sys.path.insert(0, {str(ROOT)!r}); "
        f"sys.argv = ['app.investigations', 'verify', {identity!r}, "
        f"'--store', {str(database)!r}]; "
        "runpy.run_module('app.investigations', run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", program],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "verified"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["private-marker"],
        ["list"],
        ["list", "--store", "missing", "--limit", "private-marker"],
        ["show", "private-marker", "--store", "missing", "--private-marker"],
    ],
)
def test_saved_cli_invalid_invocation_never_echoes_values(arguments: list[str]) -> None:
    """Every parser failure leaves stdout empty and keeps private arguments hidden."""
    result = run_cli("app.investigations", *arguments)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "private-marker" not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("command", ["list", "show", "verify"])
def test_read_commands_do_not_create_missing_store(
    command: str,
    tmp_path: Path,
) -> None:
    """Read-only inspection cannot create a directory, database or local history."""
    path = tmp_path / "private-marker" / "new.sqlite3"
    arguments = [command, "--store", str(path)]
    if command != "list":
        arguments.append("inv-" + "a" * 24)
    result = run_cli("app.investigations", *arguments)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "private-marker" not in result.stderr
    assert not path.parent.exists()


def test_collector_can_save_and_reopen_a_fake_daemon_report(
    fake_docker: FakeDocker, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Docker CLI composes the same store while keeping its GET-only boundary."""
    finished = stamp(NOW)
    logs = frame(1, f"{stamp(NOW)} Fictional startup.\n")
    fake_docker.container(inspect_document(finished_at=finished), logs)
    database = tmp_path / "docker.sqlite3"
    code = collect_main(
        ["--docker-socket", fake_docker.socket_path, "--store", str(database)],
        environ={},
        clock=lambda: NOW,
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    receipt = json.loads(captured.out)
    assert receipt["origin"] == "docker_lab"
    verified = run_cli(
        "app.investigations",
        "verify",
        receipt["investigation_id"],
        "--store",
        str(database),
    )
    assert verified.returncode == 0, verified.stderr
    assert len(fake_docker.requests) == 2
    assert {method for method, _ in fake_docker.requests} == {"GET"}


def test_tampered_report_fails_verification_and_cannot_be_shown(tmp_path: Path) -> None:
    """Stored hash failures produce exit 1 in verify and no report from show."""
    database = tmp_path / "tampered.sqlite3"
    saved = run_cli("app.cli", str(FIXTURE), "--store", str(database))
    assert saved.returncode == 0
    identity = json.loads(saved.stdout)["investigation_id"]
    with sqlite3.connect(database) as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'investigations_no_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER investigations_no_update")
        connection.execute(
            "UPDATE investigations SET report_document = report_document || ' '"
        )
        connection.execute(trigger)
    before = database.read_bytes()
    verified = run_cli(
        "app.investigations", "verify", identity, "--store", str(database)
    )
    assert verified.returncode == 1, verified.stderr
    assert json.loads(verified.stdout)["status"] == "failed"
    shown = run_cli("app.investigations", "show", identity, "--store", str(database))
    assert shown.returncode == 2
    assert shown.stdout == ""
    assert "integrity" in shown.stderr
    assert database.read_bytes() == before
