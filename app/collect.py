"""Collect allowlisted lab evidence from Docker and print its report or receipt."""

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from app.cli_support import (
    SafeArgumentParser,
    add_history_and_store_options,
    add_telemetry_options,
    finish,
    utc_now,
    with_deployment_history,
    with_telemetry,
)
from app.infrastructure.deployments import HistoryError
from app.infrastructure.docker_engine import (
    DockerEngineClient,
    DockerError,
    resolve_socket_path,
)
from app.infrastructure.docker_lab import (
    DEFAULT_COLLECTION_LIMITS,
    LABS,
    CollectionError,
    collect_lab,
    select_targets,
)
from app.infrastructure.sqlite_store import StoreError
from app.infrastructure.telemetry import TelemetryError


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> int:
    """Read allowlisted lab containers once and print one document or a safe error."""
    limits = DEFAULT_COLLECTION_LIMITS
    parser = SafeArgumentParser(
        prog="python -m app.collect",
        description=(
            "Read state and bounded logs of allowlisted lab containers through "
            "the local Docker socket. Only GET reads are sent."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--lab",
        choices=sorted(LABS),
        default="stage04",
        help="Allowlisted lab to read (default stage04)",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=30,
        help=f"Window ending now, 1..{limits.max_window_minutes} (default 30)",
    )
    parser.add_argument(
        "--container",
        action="append",
        default=[],
        metavar="NAME",
        help="Allowlisted container to read; repeat it, or omit it to read all",
    )
    parser.add_argument("--incident-id", metavar="ID", help="Report identifier slug")
    parser.add_argument(
        "--docker-socket",
        metavar="PATH",
        help="Unix socket path; defaults to DOCKER_HOST or the usual locations",
    )
    add_history_and_store_options(parser)
    add_telemetry_options(parser)
    arguments = parser.parse_args(argv)
    try:
        scope = LABS[arguments.lab]
        targets = select_targets(scope, arguments.container, limits)
        socket_path = resolve_socket_path(
            arguments.docker_socket, os.environ if environ is None else environ
        )
        client = DockerEngineClient(
            socket_path,
            frozenset(targets),
            timeout_seconds=limits.timeout_seconds,
            max_inspect_bytes=limits.max_inspect_bytes,
            max_log_bytes=limits.max_log_bytes,
        )
        collected_at = clock()
        prepared = collect_lab(
            client,
            collected_at=collected_at,
            window_minutes=arguments.minutes,
            requested=targets,
            incident_id=arguments.incident_id,
            scope=scope,
            limits=limits,
        )
        prepared = with_deployment_history(prepared, arguments.repository)
        prepared = with_telemetry(
            prepared, arguments.prometheus, arguments.metrics_file, arguments.traces
        )
        document = finish(prepared, arguments.store, collected_at)
    except (
        CollectionError,
        DockerError,
        HistoryError,
        StoreError,
        TelemetryError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
