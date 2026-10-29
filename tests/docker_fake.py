"""Serve canned Docker Engine API responses on a Unix socket for offline tests."""

import json
import shutil
import socketserver
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import cast

LAB_NAME = "aidevops-lab04-api"
API = "/v1.44/containers"
FICTIONAL_ENV_SECRET = "fictional-env-value-never-collected"


def frame(stream: int, line: str) -> bytes:
    """Encode one line as a Docker multiplexed log frame."""
    payload = line.encode()
    return bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload


def stamp(value: datetime) -> str:
    """Format a timestamp the way Docker prefixes log lines (nanosecond digits)."""
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def inspect_document(
    *,
    name: str = LAB_NAME,
    label: str | None = "stage04",
    status: str = "exited",
    exit_code: object = 3,
    finished_at: str = "0001-01-01T00:00:00Z",
    tty: bool = False,
) -> dict[str, object]:
    """Build an inspect body with sensitive fields the collector must drop."""
    labels = {"com.example.fictional": "fictional-label-secret"}
    if label is not None:
        labels["ai-devops-agent.lab"] = label
    return {
        "Name": f"/{name}",
        "RestartCount": 0,
        "State": {
            "Status": status,
            "ExitCode": exit_code,
            "OOMKilled": False,
            "FinishedAt": finished_at,
            "Error": "fictional-state-error-text",
        },
        "Config": {
            "Tty": tty,
            "Labels": labels,
            "Env": [f"LAB_FICTIONAL_API_TOKEN={FICTIONAL_ENV_SECRET}"],
            "Cmd": ["python", "/lab/training_api.py"],
        },
        "Mounts": [{"Source": "/fictional/host/path"}],
    }


class _Server(socketserver.ThreadingUnixStreamServer):
    """Hold canned routes and record every request the client sends."""

    daemon_threads = True
    routes: dict[str, tuple[int, bytes]]
    requests: list[tuple[str, str]]
    delay: float


class _Handler(BaseHTTPRequestHandler):
    """Answer any method from the route table so forbidden calls are visible."""

    def _answer(self) -> None:
        """Record the request and reply with its canned status and body."""
        server = cast(_Server, self.server)
        server.requests.append((self.command, self.path))
        if server.delay:
            time.sleep(server.delay)
        status, body = server.routes.get(self.path.split("?")[0], (404, b"{}"))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_DELETE = do_PUT = _answer

    def log_message(self, format: str, *args: object) -> None:
        """Keep test output quiet; Unix clients have no address to log."""


class FakeDocker:
    """Run a fake daemon at a short socket path and expose its request log."""

    def __init__(self) -> None:
        """Start serving on a fresh socket in a short temporary directory."""
        self._directory = tempfile.mkdtemp(prefix="ada-", dir="/tmp")
        self.socket_path = str(Path(self._directory) / "docker.sock")
        self._server = _Server(self.socket_path, _Handler)
        self._server.routes = {}
        self._server.requests = []
        self._server.delay = 0.0
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}
        )
        self._thread.daemon = True
        self._thread.start()

    @property
    def requests(self) -> list[tuple[str, str]]:
        """Return method and full path of every request received so far."""
        return self._server.requests

    def set_delay(self, seconds: float) -> None:
        """Delay every response to exercise the client's time budget."""
        self._server.delay = seconds

    def route(self, path: str, status: int, body: bytes) -> None:
        """Serve a fixed status and body for one request path without its query."""
        self._server.routes[path] = (status, body)

    def container(self, document: dict[str, object], logs: bytes = b"") -> None:
        """Serve inspect and log endpoints for the container named in document."""
        name = str(document["Name"]).removeprefix("/")
        self.route(f"{API}/{name}/json", 200, json.dumps(document).encode())
        self.route(f"{API}/{name}/logs", 200, logs)

    def close(self) -> None:
        """Stop serving and remove the temporary socket directory."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        shutil.rmtree(self._directory, ignore_errors=True)
