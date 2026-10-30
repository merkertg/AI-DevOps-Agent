"""Start a real Uvicorn process and run the API smoke script against it."""

import hashlib
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    """Return a loopback port that is free right now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_smoke_script_passes_against_uvicorn(tmp_path: Path) -> None:
    """The documented user path works over real HTTP with a clean environment."""
    read, write = "fictional-smoke-read-0000", "fictional-smoke-write-0000"
    port = free_port()
    server_env = {
        "PATH": os.environ.get("PATH", ""),
        "APP_ENV": "test",
        "APP_STORE_PATH": str(tmp_path / "store.sqlite3"),
        "APP_READ_TOKEN_SHA256": hashlib.sha256(read.encode()).hexdigest(),
        "APP_WRITE_TOKEN_SHA256": hashlib.sha256(write.encode()).hexdigest(),
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=tmp_path,
        env={**server_env, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/api_smoke.py"),
                "--base-url",
                f"http://127.0.0.1:{port}",
            ],
            env={
                "PATH": os.environ.get("PATH", ""),
                "SMOKE_READ_TOKEN": read,
                "SMOKE_WRITE_TOKEN": write,
            },
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    finally:
        server.terminate()
        server.wait(timeout=30)
    assert result.returncode == 0, result.stderr
    assert '"status": "passed"' in result.stdout
    assert (tmp_path / "store.sqlite3").exists()


def test_smoke_script_requires_tokens() -> None:
    """Without both tokens the script refuses to start."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/api_smoke.py")],
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "SMOKE_READ_TOKEN" in result.stderr
