"""Check record identity, hashes and verification of stored investigations."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from app.application.records import (
    INVESTIGATION_ID,
    InvestigationRecord,
    build_record,
    integrity_problems,
    verify_record,
)
from app.application.reporting import render_report
from app.application.snapshots import encode_input
from app.infrastructure.fixtures import load_fixture

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/stage05/after_deployment.json"
STORED_AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def record() -> InvestigationRecord:
    """Build the record of the Stage 05 fixture at a fixed storage time."""
    return build_record(load_fixture(FIXTURE), stored_at=STORED_AT, app_version="0.1.0")


def rehash(
    original: InvestigationRecord,
    *,
    input_document: str | None = None,
    report_document: str | None = None,
) -> InvestigationRecord:
    """Change documents and recompute their hashes, as a careful forger would."""
    changed = replace(
        original,
        input_document=input_document or original.input_document,
        report_document=report_document or original.report_document,
    )
    summary = replace(
        changed.summary,
        input_sha256=hashlib.sha256(changed.input_document.encode()).hexdigest(),
        report_sha256=hashlib.sha256(changed.report_document.encode()).hexdigest(),
    )
    return replace(changed, summary=summary)


def checks(item: InvestigationRecord) -> dict[str, str]:
    """Return the named verification results of one record."""
    return cast(dict[str, str], verify_record(item)["checks"])


def test_record_keeps_input_report_versions_and_hashes() -> None:
    """The identifier comes from the input hash and documents are exact."""
    prepared = load_fixture(FIXTURE)
    item = record()
    summary = item.summary
    assert item.input_document == encode_input(prepared)
    assert item.report_document == render_report(prepared)
    assert (item.input_version, item.report_version) == (1, 1)
    assert (
        summary.input_sha256 == hashlib.sha256(item.input_document.encode()).hexdigest()
    )
    assert summary.investigation_id == f"inv-{summary.input_sha256[:24]}"
    assert INVESTIGATION_ID.fullmatch(summary.investigation_id)
    assert summary.data_quality == "declared_complete"
    assert summary.as_json()["stored_at"] == "2026-10-29T12:00:00+00:00"


def test_same_input_gives_same_identity_at_any_storage_time() -> None:
    """Storage time is metadata; it never changes the investigation identity."""
    later = build_record(
        load_fixture(FIXTURE),
        stored_at=datetime(2027, 1, 1, tzinfo=UTC),
        app_version="0.1.0",
    )
    assert later.summary.investigation_id == record().summary.investigation_id
    assert later.report_document == record().report_document


def test_naive_storage_time_is_rejected() -> None:
    """Every stored time is explicit about its timezone."""
    with pytest.raises(ValueError, match="timezone"):
        build_record(
            load_fixture(FIXTURE), stored_at=datetime(2026, 10, 29), app_version="0"
        )


def test_fresh_record_verifies() -> None:
    """All integrity and reproduction checks pass for an unmodified record."""
    assert verify_record(record())["status"] == "verified"
    assert set(checks(record()).values()) == {"passed"}


def test_edited_documents_fail_hash_checks() -> None:
    """Changing stored bytes without updating hashes is detected."""
    item = record()
    edited = replace(item, report_document=item.report_document.replace("3", "4"))
    assert integrity_problems(edited) == ["report_sha256"]
    edited = replace(item, input_document=item.input_document + " ")
    assert integrity_problems(edited) == ["input_sha256"]
    assert verify_record(edited)["status"] == "failed"


def test_rehashed_report_edit_is_caught_by_reproduction() -> None:
    """A report changed with recomputed hashes no longer matches its input."""
    item = record()
    report = json.loads(item.report_document)
    report["facts"] = []
    forged = rehash(item, report_document=json.dumps(report))
    assert checks(forged) == {
        "input_sha256": "passed",
        "report_sha256": "passed",
        "investigation_id": "passed",
        "input_decodes": "passed",
        "metadata_matches": "passed",
        "report_reproduced": "failed",
    }


def test_rehashed_input_edit_breaks_the_identifier() -> None:
    """The identifier is bound to the input hash, so a new input needs a new ID."""
    item = record()
    forged = rehash(
        item,
        input_document=item.input_document.replace("obs-exit", "obs-exit-2"),
    )
    assert checks(forged)["investigation_id"] == "failed"
    assert checks(forged)["report_reproduced"] == "failed"


def test_undecodable_input_skips_dependent_checks() -> None:
    """Reproduction is skipped, not guessed, when the input cannot be decoded."""
    forged = rehash(record(), input_document="{}")
    assert checks(forged)["input_decodes"] == "failed"
    assert checks(forged)["report_reproduced"] == "skipped"


def test_summary_metadata_must_match_documents() -> None:
    """A summary claiming another incident or quality fails verification."""
    item = record()
    for summary in (
        replace(item.summary, incident_id="other"),
        replace(item.summary, data_quality="partial"),
    ):
        assert checks(replace(item, summary=summary))["metadata_matches"] == "failed"
