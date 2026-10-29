"""Turn prepared evidence into an immutable, hash-addressed investigation record."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.application.inputs import Origin, PreparedInput
from app.application.reporting import render_report
from app.application.snapshots import (
    INPUT_VERSION,
    SnapshotError,
    decode_input,
    encode_input,
)

REPORT_VERSION = 1
INVESTIGATION_ID = re.compile(r"inv-[0-9a-f]{24}\Z")


@dataclass(frozen=True, slots=True)
class InvestigationSummary:
    """Describe a stored investigation without carrying its documents."""

    investigation_id: str
    incident_id: str
    origin: Origin
    data_quality: str
    stored_at: datetime
    app_version: str
    input_sha256: str
    report_sha256: str

    def as_json(self) -> dict[str, object]:
        """Expose the summary fields with an ISO-8601 storage time."""
        return {
            "investigation_id": self.investigation_id,
            "incident_id": self.incident_id,
            "origin": self.origin,
            "data_quality": self.data_quality,
            "stored_at": self.stored_at.isoformat(),
            "app_version": self.app_version,
            "input_sha256": self.input_sha256,
            "report_sha256": self.report_sha256,
        }


@dataclass(frozen=True, slots=True)
class InvestigationRecord:
    """Keep the normalized input, its report, their versions and their hashes."""

    summary: InvestigationSummary
    input_version: int
    input_document: str
    report_version: int
    report_document: str


class InvestigationStore(Protocol):
    """Persist records once and read them back without modifying them."""

    def add(self, record: InvestigationRecord) -> tuple[InvestigationRecord, bool]:
        """Store a new record, or return the existing one for the same input hash."""
        ...

    def get(self, investigation_id: str) -> InvestigationRecord | None:
        """Return one stored record, or None when the identifier is unknown."""
        ...

    def recent(self, limit: int) -> tuple[InvestigationSummary, ...]:
        """Return up to limit summaries, newest first."""
        ...


def sha256_hex(document: str) -> str:
    """Hash a stored document's exact UTF-8 bytes."""
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def investigation_id_for(input_sha256: str) -> str:
    """Derive a stable identifier, so the same input always names one record."""
    return f"inv-{input_sha256[:24]}"


def build_record(
    prepared: PreparedInput, *, stored_at: datetime, app_version: str
) -> InvestigationRecord:
    """Render, encode and self-check one investigation before it is stored."""
    if stored_at.utcoffset() is None:
        raise ValueError("stored_at must include a timezone")
    report = render_report(prepared)
    document = encode_input(prepared)
    if render_report(decode_input(document)) != report:
        raise ValueError("the encoded input does not reproduce its report")
    input_sha256 = sha256_hex(document)
    quality = str(json.loads(report)["data_quality"]["status"])
    return InvestigationRecord(
        summary=InvestigationSummary(
            investigation_id=investigation_id_for(input_sha256),
            incident_id=prepared.incident.incident_id,
            origin=prepared.origin,
            data_quality=quality,
            stored_at=stored_at.astimezone(UTC),
            app_version=app_version,
            input_sha256=input_sha256,
            report_sha256=sha256_hex(report),
        ),
        input_version=INPUT_VERSION,
        input_document=document,
        report_version=REPORT_VERSION,
        report_document=report,
    )


def integrity_problems(record: InvestigationRecord) -> list[str]:
    """List hash and identifier mismatches that make a stored record untrustworthy."""
    summary = record.summary
    problems: list[str] = []
    if sha256_hex(record.input_document) != summary.input_sha256:
        problems.append("input_sha256")
    if sha256_hex(record.report_document) != summary.report_sha256:
        problems.append("report_sha256")
    if summary.investigation_id != investigation_id_for(summary.input_sha256):
        problems.append("investigation_id")
    return problems


def _stored_quality(report_document: str) -> object:
    """Read the data-quality status recorded inside a stored report, if parseable."""
    try:
        report = json.loads(report_document)
        return report["data_quality"]["status"]
    except (ValueError, TypeError, KeyError, RecursionError):
        return None


def verify_record(record: InvestigationRecord) -> dict[str, object]:
    """Check integrity, then rebuild the report from the stored input with this code."""
    problems = integrity_problems(record)
    checks: dict[str, str] = {
        "input_sha256": "failed" if "input_sha256" in problems else "passed",
        "report_sha256": "failed" if "report_sha256" in problems else "passed",
        "investigation_id": "failed" if "investigation_id" in problems else "passed",
    }
    try:
        prepared = decode_input(record.input_document)
    except SnapshotError:
        checks["input_decodes"] = "failed"
        checks["metadata_matches"] = "skipped"
        checks["report_reproduced"] = "skipped"
    else:
        checks["input_decodes"] = "passed"
        summary = record.summary
        consistent = (
            record.input_version == INPUT_VERSION
            and record.report_version == REPORT_VERSION
            and summary.incident_id == prepared.incident.incident_id
            and summary.origin == prepared.origin
            and summary.data_quality == _stored_quality(record.report_document)
        )
        checks["metadata_matches"] = "passed" if consistent else "failed"
        reproduced = render_report(prepared) == record.report_document
        checks["report_reproduced"] = "passed" if reproduced else "failed"
    verified = all(result == "passed" for result in checks.values())
    return {
        "investigation_id": record.summary.investigation_id,
        "status": "verified" if verified else "failed",
        "checks": checks,
    }
