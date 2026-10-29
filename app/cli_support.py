"""Share safe parsing, optional deployment history and storage between CLIs."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from app import __version__
from app.application.inputs import PreparedInput, add_deployment_evidence
from app.application.records import build_record
from app.application.reporting import render_report
from app.infrastructure.deployments import (
    DeploymentReader,
    HistoryError,
    HistoryLimits,
    HistoryUnavailable,
    RepositoryScope,
    TagListing,
    collect_deployments,
)
from app.infrastructure.sqlite_store import SqliteInvestigationStore, StoreError


class SafeArgumentParser(argparse.ArgumentParser):
    """Keep invalid command arguments out of diagnostics and operational reports."""

    def error(self, message: str) -> NoReturn:
        """Reject bad invocation without echoing untrusted values or local paths."""
        self.print_usage(sys.stderr)
        self.exit(2, "error: invalid command arguments; use --help\n")


class _GitUnavailable:
    """Report history as a gap when GitPython or the git executable cannot load."""

    def read_tags(self, scope: RepositoryScope, limits: HistoryLimits) -> TagListing:
        """Always raise the unavailability gap for this repository."""
        raise HistoryUnavailable(
            f"Git is not available to read repository {scope.name}; "
            "deployments are unknown."
        )


def utc_now() -> datetime:
    """Return the current time as an aware UTC timestamp."""
    return datetime.now(UTC)


def add_history_and_store_options(parser: argparse.ArgumentParser) -> None:
    """Add the optional deployment history and storage options to one CLI."""
    parser.add_argument(
        "--repository",
        type=Path,
        metavar="PATH",
        help="Allowlisted lab Git repository to read deployment tags from",
    )
    parser.add_argument(
        "--store",
        type=Path,
        metavar="PATH",
        help="SQLite file to save the investigation in; prints a receipt",
    )


def json_document(value: object) -> str:
    """Render a small result object as stable ASCII-safe JSON."""
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def with_deployment_history(
    prepared: PreparedInput, repository: Path | None
) -> PreparedInput:
    """Add deployment evidence from the allowlisted repository when one is given."""
    if repository is None:
        return prepared
    reader: DeploymentReader
    try:
        # GitPython runs git when imported, so offline runs never import it.
        from app.infrastructure.git_history import GitDeploymentReader
    except ImportError:
        reader = _GitUnavailable()
    else:
        reader = GitDeploymentReader(repository)
    evidence = collect_deployments(reader, prepared.incident)
    try:
        return add_deployment_evidence(prepared, evidence)
    except ValueError:
        raise HistoryError(
            "Deployment history conflicts with the input's sources or scope."
        ) from None


def finish(prepared: PreparedInput, store: Path | None, stored_at: datetime) -> str:
    """Return the report, or save the investigation and return its receipt."""
    if store is None:
        return render_report(prepared)
    try:
        record = build_record(prepared, stored_at=stored_at, app_version=__version__)
    except ValueError:
        raise StoreError("Investigation could not be prepared for storage.") from None
    with SqliteInvestigationStore.open(store, create=True) as investigations:
        saved, created = investigations.add(record)
    return json_document({"created": created, **saved.summary.as_json()})
