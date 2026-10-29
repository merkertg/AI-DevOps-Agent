"""Check the guarded Docker client: allowlisted GET reads, limits and safe errors."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.infrastructure.docker_engine import (
    DockerEngineClient,
    DockerError,
    DockerUnavailable,
    docker_timestamp,
    parse_log_stream,
    require_read_request,
    resolve_socket_path,
)

from docker_fake import (
    API,
    FICTIONAL_ENV_SECRET,
    LAB_NAME,
    FakeDocker,
    frame,
    inspect_document,
)

ALLOWED = frozenset({LAB_NAME})
T0 = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)


def _client(
    daemon: FakeDocker,
    *,
    timeout_seconds: float = 5.0,
    max_inspect_bytes: int = 262_144,
    max_log_bytes: int = 1_048_576,
) -> DockerEngineClient:
    """Create a client for the fake daemon with optional smaller limits."""
    return DockerEngineClient(
        daemon.socket_path,
        ALLOWED,
        timeout_seconds=timeout_seconds,
        max_inspect_bytes=max_inspect_bytes,
        max_log_bytes=max_log_bytes,
    )


@pytest.mark.parametrize("endpoint", ["json", "logs"])
def test_guard_permits_only_reads_of_allowlisted_containers(endpoint: str) -> None:
    """The two read endpoints for an allowlisted name pass the request guard."""
    require_read_request("GET", f"{API}/{LAB_NAME}/{endpoint}", {}, ALLOWED)


@pytest.mark.parametrize("method", ["POST", "DELETE", "PUT", "PATCH", "HEAD", "get"])
def test_guard_rejects_every_non_get_method(method: str) -> None:
    """Mutating or unusual HTTP methods are refused before any connection."""
    with pytest.raises(DockerError, match="Only GET"):
        require_read_request(method, f"{API}/{LAB_NAME}/json", {}, ALLOWED)


@pytest.mark.parametrize(
    "path",
    [
        f"{API}/{LAB_NAME}/restart",
        f"{API}/{LAB_NAME}/start",
        f"{API}/{LAB_NAME}/stop",
        f"{API}/{LAB_NAME}/kill",
        f"{API}/{LAB_NAME}/exec",
        f"{API}/{LAB_NAME}/archive",
        f"{API}/{LAB_NAME}/export",
        f"{API}/{LAB_NAME}/attach",
        f"{API}/{LAB_NAME}/top",
        f"{API}/json",
        f"{API}/other-container/json",
        f"{API}/../{LAB_NAME}/json",
        f"{API}/{LAB_NAME}/json/../restart",
        f"/v1.43/containers/{LAB_NAME}/json",
        f"/containers/{LAB_NAME}/json",
        "/v1.44/images/json",
        "/v1.44/exec/fictional/start",
    ],
)
def test_guard_rejects_paths_outside_the_allowlist(path: str) -> None:
    """Only exact container read paths for allowlisted names are accepted."""
    with pytest.raises(DockerError, match="path"):
        require_read_request("GET", path, {}, ALLOWED)


@pytest.mark.parametrize(
    ("endpoint", "query"),
    [("json", {"size": "1"}), ("logs", {"follow": "1"}), ("logs", {"details": "1"})],
)
def test_guard_rejects_unexpected_query_parameters(
    endpoint: str, query: dict[str, str]
) -> None:
    """Following streams or requesting extra detail is outside the read contract."""
    with pytest.raises(DockerError, match="query"):
        require_read_request("GET", f"{API}/{LAB_NAME}/{endpoint}", query, ALLOWED)


def test_client_exposes_only_inspect_and_logs() -> None:
    """The public client surface has no restart, exec, start or other operation."""
    public = {name for name in dir(DockerEngineClient) if not name.startswith("_")}
    assert public == {"inspect", "logs"}


@pytest.mark.parametrize("name", ["", "-leading", "a/b", "a b", "x" * 49, "a?b"])
def test_client_rejects_unsafe_allowlist_names(name: str) -> None:
    """Allowlisted names must be Docker-safe slugs that cannot alter the path."""
    with pytest.raises(ValueError, match="slugs"):
        DockerEngineClient("/fictional.sock", frozenset({name}))


def test_docker_timestamp_uses_utc_seconds_and_nanoseconds() -> None:
    """Window filters are exact seconds.nanoseconds values, regardless of offset."""
    local = datetime(
        2026, 10, 29, 15, 0, 0, 123456, tzinfo=timezone(timedelta(hours=5))
    )
    assert docker_timestamp(local) == f"{int(T0.timestamp())}.123456000"


def test_multiplexed_log_stream_keeps_stdout_and_stderr_in_order() -> None:
    """Frame headers are removed and lines from both streams stay chronological."""
    data = frame(1, "2026-10-29T10:00:01.000000001Z first\n") + frame(
        2, "2026-10-29T10:00:02.5Z second line\n"
    )
    batch = parse_log_stream(data, tty=False, cut=False, max_lines=10)
    assert [entry.text for entry in batch.entries] == ["first", "second line"]
    assert batch.entries[1].timestamp == T0 + timedelta(seconds=2.5)
    assert not (batch.truncated_lines or batch.truncated_bytes or batch.malformed_lines)


def test_log_stream_keeps_newest_lines_and_reports_line_truncation() -> None:
    """More than max_lines entries keep the newest and mark the rest as omitted."""
    data = b"".join(
        frame(1, f"2026-10-29T10:00:0{second}Z line-{second}\n") for second in range(4)
    )
    batch = parse_log_stream(data, tty=False, cut=False, max_lines=3)
    assert [entry.text for entry in batch.entries] == ["line-1", "line-2", "line-3"]
    assert batch.truncated_lines


def test_log_stream_counts_lines_without_valid_timestamps() -> None:
    """Lines Docker did not timestamp are skipped and counted, not invented."""
    data = frame(1, "not-a-time text\nno-space\n2026-10-29T10:00:00Z kept\n")
    batch = parse_log_stream(data, tty=False, cut=False, max_lines=10)
    assert [entry.text for entry in batch.entries] == ["kept"]
    assert batch.malformed_lines == 2


@pytest.mark.parametrize(
    "data",
    [
        b"\x05\x00\x00\x00\x00\x00\x00\x01x",
        b"\x01\x00\x01\x00\x00\x00\x00\x01x",
        b"\x01\x00\x00\x00\x00\x00\x00\x09short",
        b"\x01\x00\x00",
    ],
)
def test_malformed_multiplexed_stream_is_rejected(data: bytes) -> None:
    """Unknown stream types, padding or incomplete frames are not guessed at."""
    with pytest.raises(DockerError, match="malformed"):
        parse_log_stream(data, tty=False, cut=False, max_lines=10)


def test_frame_cut_by_byte_limit_is_dropped_and_reported() -> None:
    """A partial final frame after the byte limit is dropped, not misparsed."""
    whole = frame(1, "2026-10-29T10:00:00Z kept\n")
    partial = frame(1, "2026-10-29T10:00:01Z cut-off line\n")[:12]
    batch = parse_log_stream(whole + partial, tty=False, cut=True, max_lines=10)
    assert [entry.text for entry in batch.entries] == ["kept"]
    assert batch.truncated_bytes


def test_tty_stream_is_raw_and_cut_drops_the_partial_last_line() -> None:
    """TTY logs have no frames; CRLF ends lines and a cut line is not kept."""
    data = b"2026-10-29T10:00:00Z one\r\n2026-10-29T10:00:01Z partial-li"
    batch = parse_log_stream(data, tty=True, cut=True, max_lines=10)
    assert [entry.text for entry in batch.entries] == ["one"]


def test_invalid_utf8_in_logs_is_replaced_rather_than_failing() -> None:
    """Container output is untrusted bytes; decoding never raises."""
    raw = b"2026-10-29T10:00:00Z bad-\xff-byte\n"
    data = bytes([1, 0, 0, 0]) + len(raw).to_bytes(4, "big") + raw
    batch = parse_log_stream(data, tty=False, cut=False, max_lines=10)
    assert batch.entries[0].text == "bad-�-byte"


def test_explicit_socket_wins_and_tcp_docker_host_is_refused() -> None:
    """Only Unix sockets are supported; TCP and TLS daemons are out of scope."""
    assert resolve_socket_path("/fictional.sock", {"DOCKER_HOST": "tcp://x"}) == (
        "/fictional.sock"
    )
    assert resolve_socket_path(None, {"DOCKER_HOST": "unix:///fictional.sock"}) == (
        "/fictional.sock"
    )
    for host in ("tcp://127.0.0.1:2375", "ssh://fictional", "unix://"):
        with pytest.raises(DockerError, match="unix://"):
            resolve_socket_path(None, {"DOCKER_HOST": host})


def test_default_socket_candidates_use_home_and_report_absence(
    tmp_path: Path,
) -> None:
    """The first existing default socket is chosen; none gives a clear error."""
    missing = str(tmp_path / "missing.sock")
    home_socket = tmp_path / ".docker/run/docker.sock"
    with pytest.raises(DockerUnavailable, match="No Docker socket"):
        resolve_socket_path(
            None, {"HOME": str(tmp_path)}, (missing, "~/.docker/run/docker.sock")
        )
    home_socket.parent.mkdir(parents=True)
    home_socket.touch()
    assert resolve_socket_path(
        None, {"HOME": str(tmp_path)}, (missing, "~/.docker/run/docker.sock")
    ) == str(home_socket)
    with pytest.raises(DockerUnavailable):
        resolve_socket_path(None, {}, ("~/.docker/run/docker.sock",))


def test_inspect_copies_selected_fields_and_drops_environment(
    fake_docker: FakeDocker,
) -> None:
    """Environment, mounts, command and other labels never leave the adapter."""
    fake_docker.container(
        inspect_document(finished_at="2026-10-29T10:00:05.123456789Z")
    )
    snapshot = _client(fake_docker).inspect(LAB_NAME)
    assert snapshot is not None
    assert snapshot.name == LAB_NAME
    assert snapshot.lab_label == "stage04"
    assert (snapshot.status, snapshot.exit_code, snapshot.tty) == ("exited", 3, False)
    assert snapshot.finished_at == T0 + timedelta(seconds=5, microseconds=123456)
    text = repr(snapshot)
    for secret in (FICTIONAL_ENV_SECRET, "fictional-label-secret", "/fictional/host"):
        assert secret not in text
    assert fake_docker.requests == [("GET", f"{API}/{LAB_NAME}/json")]


@pytest.mark.parametrize(
    ("status", "exit_code", "finished", "expected"),
    [
        ("fictional-status", 3, "0001-01-01T00:00:00Z", ("unknown", 3, None)),
        ("exited", True, "not-a-time", ("exited", None, None)),
        ("running", "3", "2026-10-29T10:00:00", ("running", None, None)),
    ],
)
def test_inspect_normalizes_unexpected_state_values(
    fake_docker: FakeDocker,
    status: str,
    exit_code: object,
    finished: str,
    expected: tuple[str, int | None, None],
) -> None:
    """Unknown status, non-integer codes and zero or naive times become explicit."""
    fake_docker.container(
        inspect_document(status=status, exit_code=exit_code, finished_at=finished)
    )
    snapshot = _client(fake_docker).inspect(LAB_NAME)
    assert snapshot is not None
    assert (snapshot.status, snapshot.exit_code, snapshot.finished_at) == expected


def test_missing_container_returns_none(fake_docker: FakeDocker) -> None:
    """Docker 404 means the container is absent, which callers report as a gap."""
    client = _client(fake_docker)
    assert client.inspect(LAB_NAME) is None
    assert client.logs(LAB_NAME, since=T0, until=T0, max_lines=5, tty=False) is None


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (500, b"fictional-daemon-detail", "HTTP status 500"),
        (200, b"not json", "not valid JSON"),
        (200, b"[]", "unexpected shape"),
        (200, b'{"Name": "/x"}', "unexpected shape"),
    ],
)
def test_inspect_errors_are_safe_and_static(
    fake_docker: FakeDocker, status: int, body: bytes, message: str
) -> None:
    """Daemon error bodies and malformed documents are not echoed."""
    fake_docker.route(f"{API}/{LAB_NAME}/json", status, body)
    with pytest.raises(DockerError, match=message) as error:
        _client(fake_docker).inspect(LAB_NAME)
    assert "fictional-daemon-detail" not in str(error.value)


def test_oversized_inspect_response_is_rejected(fake_docker: FakeDocker) -> None:
    """Inspect bodies over the byte limit are refused rather than parsed."""
    fake_docker.container(inspect_document())
    with pytest.raises(DockerError, match="byte limit"):
        _client(fake_docker, max_inspect_bytes=64).inspect(LAB_NAME)


def test_logs_request_uses_fixed_query_and_window(fake_docker: FakeDocker) -> None:
    """The logs call sends only bounded, timestamped, non-following parameters."""
    fake_docker.container(
        inspect_document(), frame(1, "2026-10-29T10:00:01Z fictional line\n")
    )
    batch = _client(fake_docker).logs(
        LAB_NAME, since=T0, until=T0 + timedelta(minutes=1), max_lines=5, tty=False
    )
    assert batch is not None
    assert [entry.text for entry in batch.entries] == ["fictional line"]
    method, path = fake_docker.requests[0]
    seconds = int(T0.timestamp())
    assert method == "GET"
    assert path == (
        f"{API}/{LAB_NAME}/logs?since={seconds}.000000000&stderr=1&stdout=1"
        f"&tail=6&timestamps=1&until={seconds + 60}.000000000"
    )


def test_log_response_over_byte_limit_is_marked_truncated(
    fake_docker: FakeDocker,
) -> None:
    """Only the byte limit is read; the batch says newer bytes were not read."""
    lines = b"".join(
        frame(1, f"2026-10-29T10:00:0{second}Z fictional line {second}\n")
        for second in range(5)
    )
    fake_docker.container(inspect_document(), lines)
    batch = _client(fake_docker, max_log_bytes=100).logs(
        LAB_NAME, since=T0, until=T0, max_lines=50, tty=False
    )
    assert batch is not None
    assert batch.truncated_bytes
    assert 0 < len(batch.entries) < 5


def test_names_outside_the_allowlist_never_reach_the_socket(
    fake_docker: FakeDocker,
) -> None:
    """The guard runs before connecting, so a refused name sends no request."""
    with pytest.raises(DockerError, match="allowlist"):
        _client(fake_docker).inspect("other-container")
    assert fake_docker.requests == []


def test_unavailable_daemon_gives_a_clear_error_without_the_path(
    tmp_path: Path,
) -> None:
    """A missing or dead socket is reported without disclosing the local path."""
    path = str(tmp_path / "fictional-private-dir.sock")
    with pytest.raises(DockerUnavailable, match="unavailable") as error:
        DockerEngineClient(path, ALLOWED).inspect(LAB_NAME)
    assert "fictional-private-dir" not in str(error.value)


def test_slow_daemon_exceeds_the_time_budget(fake_docker: FakeDocker) -> None:
    """A daemon that does not answer in time produces an error, not a hang."""
    fake_docker.container(inspect_document())
    fake_docker.set_delay(0.5)
    with pytest.raises(DockerError, match="timed out"):
        _client(fake_docker, timeout_seconds=0.1).inspect(LAB_NAME)


def test_fake_daemon_never_sees_a_non_get_request(fake_docker: FakeDocker) -> None:
    """A full inspect-and-logs read uses GET only, on the two allowed paths."""
    fake_docker.container(inspect_document(), b"")
    client = _client(fake_docker)
    client.inspect(LAB_NAME)
    client.logs(LAB_NAME, since=T0, until=T0, max_lines=5, tty=False)
    assert {method for method, _ in fake_docker.requests} == {"GET"}
    assert [path.split("?")[0] for _, path in fake_docker.requests] == [
        f"{API}/{LAB_NAME}/json",
        f"{API}/{LAB_NAME}/logs",
    ]
    assert json.dumps(fake_docker.requests).count("restart") == 0
