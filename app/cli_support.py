"""Share safe parsing, optional deployment history and storage between CLIs."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from app import __version__
from app.application.inputs import (
    PreparedInput,
    add_deployment_evidence,
    add_telemetry_evidence,
)
from app.application.records import (
    INVESTIGATION_ID,
    InvestigationRecord,
    build_record,
)
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
from app.infrastructure.otlp_traces import OtlpFileReader
from app.infrastructure.prometheus import PrometheusClient, RecordedPrometheus
from app.infrastructure.sqlite_store import SqliteInvestigationStore, StoreError
from app.infrastructure.telemetry import (
    DEFAULT_TELEMETRY_LIMITS,
    STAGE07_TELEMETRY,
    MetricsReader,
    TelemetryError,
    collect_telemetry,
)


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


def add_telemetry_options(parser: argparse.ArgumentParser) -> None:
    """Add optional metrics and trace inputs to one CLI."""
    metrics = parser.add_mutually_exclusive_group()
    metrics.add_argument(
        "--prometheus",
        metavar="URL",
        help="Loopback Prometheus for allowlisted queries, e.g. http://127.0.0.1:19090",
    )
    metrics.add_argument(
        "--metrics-file",
        type=Path,
        metavar="PATH",
        help="Recorded Prometheus responses for the allowlisted queries",
    )
    parser.add_argument(
        "--traces",
        type=Path,
        metavar="PATH",
        help="OTLP JSON lines trace export of the lab collector",
    )


def with_telemetry(
    prepared: PreparedInput,
    prometheus: str | None,
    metrics_file: Path | None,
    traces: Path | None,
) -> PreparedInput:
    """Add allowlisted metrics and trace evidence when any source is given."""
    if prometheus is None and metrics_file is None and traces is None:
        return prepared
    limits = DEFAULT_TELEMETRY_LIMITS
    metrics: MetricsReader | None = None
    if prometheus is not None:
        allowed = frozenset(query.expression for query in STAGE07_TELEMETRY.queries)
        metrics = PrometheusClient(prometheus, allowed, limits)
    elif metrics_file is not None:
        metrics = RecordedPrometheus(metrics_file, limits)
    evidence = collect_telemetry(
        prepared.incident,
        metrics=metrics,
        traces=OtlpFileReader(traces) if traces is not None else None,
        limits=limits,
    )
    try:
        return add_telemetry_evidence(prepared, evidence)
    except ValueError:
        raise TelemetryError(
            "Telemetry conflicts with the input's sources or scope."
        ) from None


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


def find_record(
    store: SqliteInvestigationStore, investigation_id: str
) -> InvestigationRecord:
    """Find one record by a well-formed identifier or fail with a safe message."""
    if not INVESTIGATION_ID.fullmatch(investigation_id):
        raise StoreError(
            "Investigation ID must be inv- followed by 24 lowercase hex digits."
        )
    record = store.get(investigation_id)
    if record is None:
        raise StoreError("Investigation was not found.")
    return record
