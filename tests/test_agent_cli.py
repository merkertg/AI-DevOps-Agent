"""Run the agent CLI on saved investigations: reports, refusals, no changes."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agent.__main__ import main
from app.cli import main as save_main

from agent_support import FIXTURE, ROOT

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def save(store: Path, *extra: str) -> str:
    """Save the Stage 05 fixture and return its investigation ID."""
    code = save_main([str(FIXTURE), "--store", str(store), *extra], clock=lambda: NOW)
    assert code == 0
    with sqlite3.connect(store) as connection:
        row = connection.execute(
            "SELECT investigation_id FROM investigations ORDER BY stored_at DESC"
        ).fetchone()
    return str(row[0])


def run(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    """Run the agent CLI and capture its output."""
    capsys.readouterr()
    code = main(arguments)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_agent_reports_on_a_saved_investigation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A verified investigation yields a deterministic agent report."""
    store = tmp_path / "store.sqlite3"
    identifier = save(store)
    code, out, err = run([identifier, "--store", str(store)], capsys)
    assert (code, err) == (0, "")
    report = json.loads(out)
    assert report["subject"]["investigation_id"] == identifier
    assert report["agent"]["status"] == "answered"
    assert {item["status"] for item in report["hypotheses"]} == {"unconfirmed"}
    assert report["next_check"]["executed"] is False
    assert run([identifier, "--store", str(store)], capsys)[1] == out


def test_agent_uses_saved_deployment_history(
    tmp_path: Path, lab_repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With history saved, the deployment hypothesis and check appear."""
    store = tmp_path / "store.sqlite3"
    identifier = save(store, "--repository", str(lab_repository))
    report = json.loads(run([identifier, "--store", str(store)], capsys)[1])
    assert report["next_check"]["kind"] == "compare_deployed_revision"
    assert report["hypotheses"][0]["fact_ids"] == ["fact-001", "fact-002"]


def test_agent_does_not_change_the_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The store is opened read-only and its bytes stay the same."""
    store = tmp_path / "store.sqlite3"
    identifier = save(store)
    before = hashlib.sha256(store.read_bytes()).hexdigest()
    assert run([identifier, "--store", str(store)], capsys)[0] == 0
    assert hashlib.sha256(store.read_bytes()).hexdigest() == before


def test_agent_refuses_an_investigation_that_does_not_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rehashed but altered report fails reproduction, so the agent stops."""
    store = tmp_path / "store.sqlite3"
    identifier = save(store)
    with sqlite3.connect(store) as connection:
        report = connection.execute(
            "SELECT report_document FROM investigations"
        ).fetchone()[0]
        forged = report.replace("connection_refused", "connection_reset")
        connection.execute("DROP TRIGGER investigations_no_update")
        connection.execute(
            "UPDATE investigations SET report_document = ?, report_sha256 = ?",
            (forged, hashlib.sha256(forged.encode()).hexdigest()),
        )
        connection.execute(
            "CREATE TRIGGER investigations_no_update BEFORE UPDATE ON investigations "
            "BEGIN SELECT RAISE(ABORT, 'investigations are append-only'); END"
        )
    code, out, err = run([identifier, "--store", str(store)], capsys)
    assert (code, out) == (2, "")
    assert "did not verify" in err


@pytest.mark.parametrize(
    "identifier", ["inv-000000000000000000000000", "fictional-private-marker"]
)
def test_unknown_or_malformed_ids_are_refused(
    identifier: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing records and bad IDs exit 2 without echoing the argument."""
    store = tmp_path / "store.sqlite3"
    save(store)
    code, out, err = run([identifier, "--store", str(store)], capsys)
    assert (code, out) == (2, "")
    assert "fictional-private-marker" not in err


def test_missing_store_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing store is an error, and reading does not create it."""
    store = tmp_path / "missing.sqlite3"
    code, out, err = run(["inv-" + "0" * 24, "--store", str(store)], capsys)
    assert (code, out) == (2, "")
    assert "not found" in err
    assert not store.exists()


@pytest.mark.parametrize(
    "arguments", [[], ["--fictional-private-marker"], ["inv-x", "--provider", "llm"]]
)
def test_invocation_errors_do_not_echo_arguments(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Argument errors use the static diagnostic of the other CLIs."""
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "invalid command arguments" in captured.err
    assert "fictional-private-marker" not in captured.err


def test_module_runs_as_a_separate_process(tmp_path: Path) -> None:
    """python -m app.agent works with the locked environment."""
    store = tmp_path / "store.sqlite3"
    identifier = save(store)
    result = subprocess.run(
        [sys.executable, "-m", "app.agent", identifier, "--store", str(store)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["agent"]["provider"] == "offline-rules-v1"
