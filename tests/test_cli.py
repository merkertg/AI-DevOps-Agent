"""Exercise offline CLI success, safe failures and independence from web settings."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.cli import main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "fixtures/stage03/connection_failure.json"


def test_cli_prints_one_json_report_and_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A valid fictional fixture produces one complete stdout document and no errors."""
    assert main([str(EXAMPLE)]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["schema_version"] == 1
    assert len(report["facts"]) == 2
    assert captured.err == ""


@pytest.mark.parametrize(
    "content", [b"not-json fictional-private-marker", b"\xfffictional-private-marker"]
)
def test_cli_rejects_bad_input_without_echoing_content_or_path(
    content: bytes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Malformed input errors leave stdout empty and hide untrusted details."""
    path = tmp_path / "fictional-private-marker.json"
    path.write_bytes(content)
    assert main([str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert "fictional-private-marker" not in captured.err
    assert "Traceback" not in captured.err


def test_cli_missing_file_has_safe_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Filesystem errors do not disclose local paths or raw exception messages."""
    assert main([str(tmp_path / "fictional-sensitive-name.json")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert "fictional-sensitive-name" not in captured.err


@pytest.mark.parametrize(
    "arguments",
    [[], ["--fictional-private-marker"], [str(EXAMPLE), "fictional-private-marker"]],
)
def test_cli_invocation_errors_do_not_echo_arguments(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Argument parsing fails with a static diagnostic rather than repeating input."""
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid command arguments" in captured.err
    assert "fictional-private-marker" not in captured.err


def test_cli_module_works_without_site_packages_or_env_configuration(
    tmp_path: Path,
) -> None:
    """The real module path runs offline using stdlib even beside an invalid .env."""
    (tmp_path / ".env").write_text("APP_ENV=invalid-value\nAPP_DEBUG=invalid-value\n")
    program = (
        f"import sys, runpy; sys.path.insert(0, {str(ROOT)!r}); "
        f"sys.argv = ['app.cli', {str(EXAMPLE)!r}]; "
        "runpy.run_module('app.cli', run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", program],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert len(json.loads(result.stdout)["timeline"]) == 3
    assert result.stderr == ""


def test_real_module_invocation_reports_fixture_failure_without_traceback(
    tmp_path: Path,
) -> None:
    """A subprocess receives the documented exit code rather than an uncaught error."""
    path = tmp_path / "broken.json"
    path.write_text('{"schema_version": 1,')
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
