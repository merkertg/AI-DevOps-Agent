"""Run the bounded offline agent on one saved, verified investigation."""

import sys
from collections.abc import Sequence
from pathlib import Path

from app.agent.contracts import AgentError
from app.agent.graph import render_agent_report, run_agent
from app.agent.offline import OfflineRulesProvider
from app.agent.tools import build_view
from app.application.records import verify_record
from app.application.snapshots import decode_input
from app.cli_support import SafeArgumentParser, find_record
from app.infrastructure.sqlite_store import SqliteInvestigationStore, StoreError


def main(argv: Sequence[str] | None = None) -> int:
    """Verify the saved input, run the agent and print its validated report."""
    parser = SafeArgumentParser(
        prog="python -m app.agent",
        description=(
            "Run the bounded offline agent on a saved investigation. It reads "
            "only that investigation through read-only tools and proposes "
            "unconfirmed hypotheses and one diagnostic check."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("investigation_id", metavar="ID", help="Saved investigation")
    parser.add_argument(
        "--store", type=Path, required=True, metavar="PATH", help="SQLite store file"
    )
    arguments = parser.parse_args(argv)
    try:
        with SqliteInvestigationStore.open(arguments.store, create=False) as store:
            record = find_record(store, arguments.investigation_id)
        if verify_record(record)["status"] != "verified":
            raise AgentError(
                "Saved investigation did not verify; the agent was not run."
            )
        view = build_view(
            decode_input(record.input_document), record.summary.investigation_id
        )
        report = run_agent(view, OfflineRulesProvider())
    except (AgentError, StoreError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(render_agent_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
