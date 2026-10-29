"""Investigate one bounded fictional fixture and print its report or saved receipt."""

import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from app.cli_support import (
    SafeArgumentParser,
    add_history_and_store_options,
    finish,
    utc_now,
    with_deployment_history,
)
from app.infrastructure.deployments import HistoryError
from app.infrastructure.fixtures import FixtureError, load_fixture
from app.infrastructure.sqlite_store import StoreError


def main(
    argv: Sequence[str] | None = None, *, clock: Callable[[], datetime] = utc_now
) -> int:
    """Compose intake, optional history and storage with a predictable exit status."""
    parser = SafeArgumentParser(
        prog="python -m app.cli",
        description=(
            "Investigate one local fictional JSON fixture without a network, "
            "optionally adding lab deployment history and saving the result."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "fixture", type=Path, metavar="FIXTURE", help="Regular UTF-8 JSON fixture file"
    )
    add_history_and_store_options(parser)
    arguments = parser.parse_args(argv)
    try:
        prepared = with_deployment_history(
            load_fixture(arguments.fixture), arguments.repository
        )
        document = finish(prepared, arguments.store, clock())
    except (FixtureError, HistoryError, StoreError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
