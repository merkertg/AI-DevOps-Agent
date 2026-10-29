"""Read one bounded fictional fixture and print its sanitized offline JSON report."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from app.application.reporting import render_report
from app.infrastructure.fixtures import FixtureError, load_fixture


class _SafeArgumentParser(argparse.ArgumentParser):
    """Keep invalid command arguments out of diagnostics and operational reports."""

    def error(self, message: str) -> NoReturn:
        """Reject bad invocation without echoing untrusted values or local paths."""
        self.print_usage(sys.stderr)
        self.exit(2, "error: invalid command arguments; use --help\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Compose fixture intake and reporting with a safe, predictable exit status."""
    parser = _SafeArgumentParser(
        prog="python -m app.cli",
        description="Investigate one local fictional JSON fixture without a network.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "fixture", type=Path, metavar="FIXTURE", help="Regular UTF-8 JSON fixture file"
    )
    arguments = parser.parse_args(argv)
    try:
        prepared = load_fixture(arguments.fixture)
    except FixtureError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(render_report(prepared))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
