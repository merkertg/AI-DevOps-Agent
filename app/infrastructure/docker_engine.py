"""Read selected container state and bounded logs through a guarded Docker socket.

The client sends only allowlisted GET requests. This guards the collector's own
code; it does not reduce the privileges of the Docker socket. See ADR 0002.
"""

import http.client
import json
import os
import re
import socket
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

API_VERSION = "v1.44"
LAB_LABEL = "ai-devops-agent.lab"
CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,47}\Z")
DEFAULT_SOCKETS = ("/var/run/docker.sock", "~/.docker/run/docker.sock")

_READ_PATH = re.compile(
    rf"/{re.escape(API_VERSION)}/containers/"
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]{0,47})/(?P<endpoint>json|logs)\Z"
)
_QUERY_KEYS = {
    "json": frozenset(),
    "logs": frozenset({"stdout", "stderr", "timestamps", "since", "until", "tail"}),
}
_STATUSES = frozenset(
    {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_FRAME_HEADER = 8
_READ_CHUNK = 65_536


class DockerError(ValueError):
    """Report a refused or failed Docker read with a static, safe diagnostic."""


class DockerUnavailable(DockerError):
    """Signal that no Docker daemon answered on the configured Unix socket."""


@dataclass(frozen=True, slots=True)
class ContainerSnapshot:
    """Keep only the container state fields the lab collector may report."""

    name: str
    lab_label: str | None
    status: str
    exit_code: int | None
    oom_killed: bool
    restart_count: int
    finished_at: datetime | None
    tty: bool


@dataclass(frozen=True, slots=True)
class LogEntry:
    """Pair one log line's Docker receipt timestamp with its undecoded text."""

    timestamp: datetime
    text: str


@dataclass(frozen=True, slots=True)
class LogBatch:
    """Return bounded log entries and say which bounds or format issues applied."""

    entries: tuple[LogEntry, ...]
    truncated_lines: bool = False
    truncated_bytes: bool = False
    malformed_lines: int = 0


def require_read_request(
    method: str, path: str, query: Mapping[str, str], allowed: frozenset[str]
) -> None:
    """Refuse every Docker API request except reads of allowlisted containers."""
    if method != "GET":
        raise DockerError("Only GET requests to the Docker API are permitted.")
    match = _READ_PATH.fullmatch(path)
    if match is None or match["name"] not in allowed:
        raise DockerError("Docker API path is outside the collector allowlist.")
    if not query.keys() <= _QUERY_KEYS[match["endpoint"]]:
        raise DockerError("Docker API query is outside the collector allowlist.")


def resolve_socket_path(
    explicit: str | None,
    environ: Mapping[str, str],
    candidates: Sequence[str] = DEFAULT_SOCKETS,
) -> str:
    """Choose an explicit, DOCKER_HOST or default Unix socket; refuse TCP hosts."""
    if explicit:
        return explicit
    host = environ.get("DOCKER_HOST", "")
    if host:
        if not host.startswith("unix://") or len(host) == len("unix://"):
            raise DockerError("Only unix:// DOCKER_HOST values are supported.")
        return host.removeprefix("unix://")
    home = environ.get("HOME")
    for candidate in candidates:
        if candidate.startswith("~/"):
            if not home:
                continue
            candidate = str(Path(home) / candidate.removeprefix("~/"))
        if os.path.exists(candidate):
            return candidate
    raise DockerUnavailable(
        "No Docker socket was found; start Docker or pass --docker-socket."
    )


def docker_timestamp(value: datetime) -> str:
    """Format an aware time as the seconds.nanoseconds value Docker filters accept."""
    delta = value.astimezone(UTC) - _EPOCH
    seconds = delta.days * 86_400 + delta.seconds
    return f"{seconds}.{delta.microseconds * 1000:09d}"


def _parse_time(value: object) -> datetime | None:
    """Parse a Docker RFC 3339 time; the zero time means it never happened."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() is None or parsed.year <= 1:
        return None
    return parsed.astimezone(UTC)


def _snapshot(document: object) -> ContainerSnapshot:
    """Copy selected inspect fields; environment, mounts and command are dropped."""
    if not isinstance(document, dict):
        raise DockerError("Docker inspect response has an unexpected shape.")
    state = document.get("State")
    config = document.get("Config")
    name = document.get("Name")
    if not isinstance(state, dict) or not isinstance(name, str):
        raise DockerError("Docker inspect response has an unexpected shape.")
    if not isinstance(config, dict):
        config = {}
    labels = config.get("Labels")
    label = labels.get(LAB_LABEL) if isinstance(labels, dict) else None
    status = state.get("Status")
    exit_code = state.get("ExitCode")
    restart_count = document.get("RestartCount")
    return ContainerSnapshot(
        name=name.removeprefix("/"),
        lab_label=label if isinstance(label, str) else None,
        status=status if status in _STATUSES else "unknown",
        exit_code=exit_code if type(exit_code) is int else None,
        oom_killed=state.get("OOMKilled") is True,
        restart_count=(
            restart_count if type(restart_count) is int and restart_count >= 0 else 0
        ),
        finished_at=_parse_time(state.get("FinishedAt")),
        tty=config.get("Tty") is True,
    )


def _payloads(data: bytes, tty: bool, cut: bool) -> list[bytes]:
    """Split Docker's multiplexed log stream; drop only a frame cut by the limit."""
    if tty:
        return [data[: data.rfind(b"\n") + 1] if cut else data]
    payloads: list[bytes] = []
    offset = 0
    while offset < len(data):
        header = data[offset : offset + _FRAME_HEADER]
        size = int.from_bytes(header[4:8], "big") if len(header) == 8 else 0
        end = offset + _FRAME_HEADER + size
        if len(header) < _FRAME_HEADER or end > len(data):
            if cut:
                break
            raise DockerError("Docker log stream is malformed.")
        if header[0] not in (0, 1, 2) or header[1:4] != b"\0\0\0":
            raise DockerError("Docker log stream is malformed.")
        payloads.append(data[offset + _FRAME_HEADER : end])
        offset = end
    return payloads


def parse_log_stream(data: bytes, *, tty: bool, cut: bool, max_lines: int) -> LogBatch:
    """Decode timestamped log lines, keep the newest max_lines and count bad lines."""
    entries: list[LogEntry] = []
    malformed = 0
    for payload in _payloads(data, tty, cut):
        for raw in payload.decode("utf-8", errors="replace").split("\n"):
            line = raw.removesuffix("\r")
            if not line:
                continue
            stamp, separator, text = line.partition(" ")
            timestamp = _parse_time(stamp) if separator else None
            if timestamp is None:
                malformed += 1
                continue
            entries.append(LogEntry(timestamp, text))
    return LogBatch(
        entries=tuple(entries[-max_lines:]),
        truncated_lines=len(entries) > max_lines,
        truncated_bytes=cut,
        malformed_lines=malformed,
    )


class _UnixConnection(http.client.HTTPConnection):
    """Speak HTTP/1.1 to a local Unix socket instead of a TCP host."""

    def __init__(self, socket_path: str, timeout: float) -> None:
        """Remember the socket path; HTTP needs a Host header but no network."""
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        """Open the Unix stream socket with the configured operation timeout."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._socket_path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class DockerEngineClient:
    """Read allowlisted containers only; there is no method that changes Docker."""

    def __init__(
        self,
        socket_path: str,
        allowed: frozenset[str],
        *,
        timeout_seconds: float = 5.0,
        max_inspect_bytes: int = 262_144,
        max_log_bytes: int = 1_048_576,
    ) -> None:
        """Fix the socket, container allowlist, time budget and response limits."""
        if any(not CONTAINER_NAME.fullmatch(name) for name in allowed):
            raise ValueError("Allowlisted container names must be Docker-safe slugs.")
        self._socket_path = socket_path
        self._allowed = frozenset(allowed)
        self._timeout = timeout_seconds
        self._max_inspect_bytes = max_inspect_bytes
        self._max_log_bytes = max_log_bytes

    def inspect(self, name: str) -> ContainerSnapshot | None:
        """Return selected state fields, or None when Docker has no such container."""
        result = self._get(name, "json", {}, self._max_inspect_bytes)
        if result is None:
            return None
        body, cut = result
        if cut:
            raise DockerError("Docker inspect response exceeded the byte limit.")
        try:
            document: object = json.loads(body)
        except (ValueError, RecursionError):
            raise DockerError("Docker inspect response is not valid JSON.") from None
        return _snapshot(document)

    def logs(
        self,
        name: str,
        *,
        since: datetime,
        until: datetime,
        max_lines: int,
        tty: bool,
    ) -> LogBatch | None:
        """Return at most max_lines newest log lines in the window, or None if gone."""
        query = {
            "stdout": "1",
            "stderr": "1",
            "timestamps": "1",
            "since": docker_timestamp(since),
            "until": docker_timestamp(until),
            "tail": str(max_lines + 1),
        }
        result = self._get(name, "logs", query, self._max_log_bytes)
        if result is None:
            return None
        body, cut = result
        return parse_log_stream(body, tty=tty, cut=cut, max_lines=max_lines)

    def _get(
        self, name: str, endpoint: str, query: Mapping[str, str], limit: int
    ) -> tuple[bytes, bool] | None:
        """Send one guarded GET and read at most limit bytes within the time budget."""
        path = f"/{API_VERSION}/containers/{name}/{endpoint}"
        require_read_request("GET", path, query, self._allowed)
        target = f"{path}?{urlencode(sorted(query.items()))}" if query else path
        deadline = time.monotonic() + self._timeout
        connection = _UnixConnection(self._socket_path, self._timeout)
        try:
            try:
                connection.connect()
            except OSError:
                raise DockerUnavailable(
                    "Docker daemon is unavailable at the configured socket."
                ) from None
            try:
                connection.request("GET", target, headers={"Accept": "*/*"})
                response = connection.getresponse()
                if response.status == 404:
                    return None
                if response.status != 200:
                    raise DockerError(
                        f"Docker API returned HTTP status {response.status}."
                    )
                return _read_limited(response, limit, deadline)
            except (OSError, http.client.HTTPException):
                raise DockerError(
                    "Docker API request failed, timed out or was malformed."
                ) from None
        finally:
            connection.close()


def _read_limited(
    response: http.client.HTTPResponse, limit: int, deadline: float
) -> tuple[bytes, bool]:
    """Read at most limit bytes, reporting whether more data was available."""
    chunks: list[bytes] = []
    size = 0
    while size <= limit:
        if time.monotonic() > deadline:
            raise DockerError("Docker API response exceeded the time budget.")
        chunk = response.read1(min(_READ_CHUNK, limit + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    data = b"".join(chunks)
    return data[:limit], size > limit
