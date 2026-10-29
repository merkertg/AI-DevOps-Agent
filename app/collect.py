"""Collect allowlisted Stage 04 lab evidence from Docker and print its JSON report."""

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import NoReturn

from app.application.reporting import render_report
from app.infrastructure.docker_engine import (
    DockerEngineClient,
    DockerError,
    resolve_socket_path,
)
from app.infrastructure.docker_lab import (
    DEFAULT_COLLECTION_LIMITS,
    STAGE04_LAB,
    CollectionError,
    collect_lab,
    select_targets,
)


class _SafeArgumentParser(argparse.ArgumentParser):
    """Keep invalid command arguments out of diagnostics and operational reports."""

    def error(self, message: str) -> NoReturn:
        """Reject bad invocation without echoing untrusted values or local paths."""
        self.print_usage(sys.stderr)
        self.exit(2, "error: invalid command arguments; use --help\n")


def _utc_now() -> datetime:
    """Return the collection time as an aware UTC timestamp."""
    return datetime.now(UTC)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> int:
    """Read allowlisted lab containers once and print one report or a safe error."""
    limits = DEFAULT_COLLECTION_LIMITS
    parser = _SafeArgumentParser(
        prog="python -m app.collect",
        description=(
            "Read state and bounded logs of allowlisted Stage 04 lab containers "
            "through the local Docker socket. Only GET reads are sent."
        ),
        allow_abbrev=False,
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
    arguments = parser.parse_args(argv)
    try:
        targets = select_targets(STAGE04_LAB, arguments.container, limits)
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
        prepared = collect_lab(
            client,
            collected_at=clock(),
            window_minutes=arguments.minutes,
            requested=targets,
            incident_id=arguments.incident_id,
            limits=limits,
        )
    except (CollectionError, DockerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(render_report(prepared))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
