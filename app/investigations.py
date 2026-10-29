"""List, show and verify saved investigations without changing the store."""

import sys
from collections.abc import Sequence
from pathlib import Path

from app.application.records import integrity_problems, verify_record
from app.cli_support import SafeArgumentParser, find_record, json_document
from app.infrastructure.sqlite_store import (
    MAX_RECENT,
    SqliteInvestigationStore,
    StoreError,
)


def _parser() -> SafeArgumentParser:
    """Describe the read-only list, show and verify commands."""
    parser = SafeArgumentParser(
        prog="python -m app.investigations",
        description="Read investigations saved with --store. The store is opened "
        "read-only.",
        allow_abbrev=False,
    )
    store = SafeArgumentParser(add_help=False, allow_abbrev=False)
    store.add_argument(
        "--store", type=Path, required=True, metavar="PATH", help="SQLite store file"
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    listing = commands.add_parser(
        "list", parents=[store], allow_abbrev=False, help="Newest summaries first"
    )
    listing.add_argument(
        "--limit", type=int, default=20, help=f"1..{MAX_RECENT} (default 20)"
    )
    for name, text in (
        ("show", "Print the stored report exactly as saved"),
        ("verify", "Check hashes and rebuild the report from the stored input"),
    ):
        command = commands.add_parser(
            name, parents=[store], allow_abbrev=False, help=text
        )
        command.add_argument("investigation_id", metavar="ID")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one read-only command; exit 1 only when verification fails."""
    arguments = _parser().parse_args(argv)
    code = 0
    try:
        with SqliteInvestigationStore.open(arguments.store, create=False) as store:
            if arguments.command == "list":
                summaries = store.recent(arguments.limit)
                document = json_document(
                    {
                        "total": store.count(),
                        "investigations": [item.as_json() for item in summaries],
                    }
                )
            elif arguments.command == "show":
                record = find_record(store, arguments.investigation_id)
                if integrity_problems(record):
                    raise StoreError(
                        "Stored investigation failed its integrity check; "
                        "run verify for details."
                    )
                document = record.report_document
            else:
                result = verify_record(find_record(store, arguments.investigation_id))
                document = json_document(result)
                code = 0 if result["status"] == "verified" else 1
    except StoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(document)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
