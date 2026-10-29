"""Run allowlisted range queries against a loopback Prometheus, or a recording."""

import http.client
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlencode, urlsplit

from app.infrastructure.telemetry import (
    MetricQuery,
    MetricSample,
    TelemetryError,
    TelemetryLimits,
    TelemetryUnavailable,
    read_regular_file,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
QUERY_RANGE_PATH = "/api/v1/query_range"


def _unavailable(reason: str) -> TelemetryUnavailable:
    """Describe why metrics are unknown without repeating response bodies."""
    return TelemetryUnavailable(f"Prometheus {reason}; metrics are unknown.")


def parse_loopback_url(url: str) -> tuple[str, int]:
    """Accept only http://<loopback host>:<port> with no path, query or user."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise TelemetryError(
            "Prometheus must be a loopback URL such as http://127.0.0.1:19090."
        ) from None
    if (
        parts.scheme != "http"
        or parts.hostname not in LOOPBACK_HOSTS
        or port is None
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise TelemetryError(
            "Prometheus must be a loopback URL such as http://127.0.0.1:19090."
        )
    return parts.hostname, port


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject repeated keys instead of letting the last one win."""
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def parse_matrix(
    payload: object, limits: TelemetryLimits
) -> tuple[tuple[MetricSample, ...], ...]:
    """Read a successful matrix response into series of time-ordered samples."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise _unavailable("returned an unsuccessful or malformed response")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "matrix":
        raise _unavailable("returned a response that is not a range matrix")
    result = data.get("result")
    if not isinstance(result, list):
        raise _unavailable("returned a malformed range matrix")
    series: list[tuple[MetricSample, ...]] = []
    for item in cast(list[object], result):
        values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(values, list) or len(values) > limits.max_points:
            raise _unavailable("returned a malformed or oversized series")
        samples: list[MetricSample] = []
        for point in cast(list[object], values):
            if not (
                isinstance(point, list)
                and len(point) == 2
                and type(point[0]) in {int, float}
                and isinstance(point[1], str)
            ):
                raise _unavailable("returned a malformed sample")
            try:
                at = datetime.fromtimestamp(point[0], UTC)
                value = float(point[1])
            except (OverflowError, OSError, ValueError):
                raise _unavailable("returned a malformed sample") from None
            samples.append(MetricSample(at, value))
        series.append(tuple(sorted(samples, key=lambda sample: sample.at)))
    return tuple(series)


def _decode(
    data: bytes, limits: TelemetryLimits
) -> tuple[tuple[MetricSample, ...], ...]:
    """Parse one JSON response body with duplicate-key and number checks."""
    try:
        payload: object = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        raise _unavailable("returned a response that is not valid JSON") from None
    return parse_matrix(payload, limits)


class PrometheusClient:
    """Send only GET range queries whose expressions are in the allowlist."""

    def __init__(
        self,
        url: str,
        allowed: frozenset[str],
        limits: TelemetryLimits,
    ) -> None:
        """Validate the loopback URL and remember the allowlisted expressions."""
        self._host, self._port = parse_loopback_url(url)
        self._allowed = allowed
        self._limits = limits

    def query_range(
        self, query: MetricQuery, start: datetime, end: datetime, step_seconds: int
    ) -> tuple[tuple[MetricSample, ...], ...]:
        """Run one allowlisted query over the window within time and byte limits."""
        if query.expression not in self._allowed:
            raise TelemetryError("The metric query is not allowlisted.")
        parameters = urlencode(
            {
                "query": query.expression,
                "start": f"{start.timestamp():.3f}",
                "end": f"{end.timestamp():.3f}",
                "step": f"{step_seconds}s",
            }
        )
        deadline = time.monotonic() + self._limits.timeout_seconds
        connection = http.client.HTTPConnection(
            self._host, self._port, timeout=self._limits.timeout_seconds
        )
        try:
            connection.request(
                "GET",
                f"{QUERY_RANGE_PATH}?{parameters}",
                headers={"Accept": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise _unavailable(f"returned HTTP status {response.status}")
            chunks: list[bytes] = []
            size = 0
            while True:
                if time.monotonic() > deadline:
                    raise _unavailable("did not answer within the time limit")
                chunk = response.read1(65_536)
                if not chunk:
                    break
                size += len(chunk)
                if size > self._limits.max_response_bytes:
                    raise _unavailable("returned a response over the size limit")
                chunks.append(chunk)
        except (OSError, http.client.HTTPException):
            raise _unavailable("was unreachable or the request failed") from None
        finally:
            connection.close()
        return _decode(b"".join(chunks), self._limits)


class RecordedPrometheus:
    """Answer allowlisted queries from recorded responses in a local JSON file."""

    def __init__(self, path: Path, limits: TelemetryLimits) -> None:
        """Remember the recording and limits; the file is read on first use."""
        self._path = path
        self._limits = limits
        self._responses: dict[str, object] | None = None

    def _load(self) -> dict[str, object]:
        """Read the bounded recording once."""
        if self._responses is None:
            try:
                data = read_regular_file(self._path, self._limits.max_response_bytes)
                payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
            except (OSError, ValueError, RecursionError):
                raise TelemetryUnavailable(
                    "Recorded metrics were not found or are invalid; metrics are "
                    "unknown."
                ) from None
            queries = payload.get("queries") if isinstance(payload, dict) else None
            if not isinstance(queries, dict):
                raise TelemetryUnavailable(
                    "Recorded metrics have no queries object; metrics are unknown."
                )
            self._responses = cast(dict[str, object], queries)
        return self._responses

    def query_range(
        self, query: MetricQuery, start: datetime, end: datetime, step_seconds: int
    ) -> tuple[tuple[MetricSample, ...], ...]:
        """Return the recorded response for the query's allowlisted name."""
        response = self._load().get(query.name)
        if response is None:
            return ()
        return parse_matrix(response, self._limits)
