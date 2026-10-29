"""Check the canonical stored-input document: round trips and strict rejection."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.application.inputs import PreparedInput
from app.application.reporting import render_report
from app.application.snapshots import SnapshotError, decode_input, encode_input
from app.domain import Incident, Observation, ObservationKind, Source, SourceKind
from app.infrastructure.fixtures import load_fixture

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted(ROOT.glob("fixtures/stage0[35]/*.json"))


def lab_like() -> PreparedInput:
    """Build a Docker-lab style input in timeline order with microsecond times."""
    end = datetime(2026, 10, 29, 10, 30, 0, 123456, tzinfo=UTC)
    log = Source("lab-api-log", "training-api", SourceKind.APPLICATION_LOG)
    history = Source("lab-deployments", "training-api", SourceKind.DEPLOYMENT_HISTORY)
    return PreparedInput(
        incident=Incident("lab-1", "training-api", end - timedelta(minutes=30), end),
        sources=(log, history),
        observations=(
            Observation(
                "lab-deploy-0002",
                "lab-deployments",
                end - timedelta(minutes=10),
                end - timedelta(minutes=9),
                ObservationKind.DEPLOYMENT,
            ),
            Observation(
                "lab-api-log-0001",
                "lab-api-log",
                end - timedelta(seconds=5, microseconds=7),
                end,
                ObservationKind.NOTE,
                message='Fictional "quoted" text\nwith a newline and café.',
            ),
        ),
        declared_complete=False,
        declared_truncated=True,
        gaps=("Fictional gap one.", "Fictional gap two."),
        truncated_observation_ids=("lab-api-log-0001",),
        truncated_gap_count=1,
        redaction_count=2,
        origin="docker_lab",
        history_repository="training-api-lab",
    )


def inputs() -> list[PreparedInput]:
    """Collect every committed fixture plus the Docker-lab style example."""
    return [load_fixture(path) for path in FIXTURES] + [lab_like()]


@pytest.mark.parametrize("index", range(len(FIXTURES) + 1))
def test_round_trip_preserves_every_field_and_the_report(index: int) -> None:
    """Decoding the encoded input returns equal values and the same report bytes."""
    prepared = inputs()[index]
    document = encode_input(prepared)
    decoded = decode_input(document)
    assert decoded == prepared
    assert encode_input(decoded) == document
    assert render_report(decoded) == render_report(prepared)


def test_encoding_is_independent_of_construction_order() -> None:
    """Sources, observations and truncated IDs are written in canonical order."""
    prepared = lab_like()
    shuffled = replace(
        prepared,
        sources=tuple(reversed(prepared.sources)),
        observations=tuple(reversed(prepared.observations)),
    )
    assert encode_input(shuffled) == encode_input(prepared)


def test_document_is_compact_ascii_with_sorted_keys() -> None:
    """One byte representation exists for each input."""
    document = encode_input(lab_like())
    assert document.isascii()
    assert "\n" not in document and ": " not in document
    payload = json.loads(document)
    assert list(payload) == sorted(payload)
    assert payload["document"] == "ai-devops-agent.prepared-input"
    assert payload["version"] == 1


def mutate(change: str) -> str:
    """Return the lab-like document with one textual change applied."""
    document = encode_input(lab_like())
    old, new = change.split(" => ")
    assert old in document
    return document.replace(old, new, 1)


@pytest.mark.parametrize(
    "document",
    [
        mutate('"version":1 => "version":2'),
        mutate('"version":1 => "version":true'),
        mutate('"document":"ai-devops-agent.prepared-input" => "document":"other"'),
        mutate('"origin":"docker_lab" => "origin":"cloud"'),
        mutate('"redaction_count":2 => "redaction_count":-1'),
        mutate('"redaction_count":2 => "redaction_count":2.0'),
        mutate('"declared_truncated":true => "declared_truncated":1'),
        mutate('"exit_code":null => "exit_code":false'),
        mutate('"kind":"deployment" => "kind":"deploy"'),
        mutate('"history_repository":"training-api-lab" => "history_repository":""'),
        mutate('"collection":{ => "collection":{"extra":1,'),
        mutate('"gaps":[ => "gaps":[1,'),
        mutate('"version":1 => "version":1,"version":1'),
        mutate("+00:00 => Z"),
        mutate('{"collection" => {  "collection"'),
        mutate('"ends_at":"2026-10-29T10:30:00.123456+00:00" => "ends_at":"x"'),
        mutate(
            '"ends_at":"2026-10-29T10:30:00.123456+00:00" '
            '=> "ends_at":"2026-10-29T10:01:00+00:00"'
        ),
        "[]",
        "not json",
        "NaN",
        "[" * 50_000,
    ],
    ids=[
        "future-version",
        "boolean-version",
        "other-document",
        "unknown-origin",
        "negative-counter",
        "float-counter",
        "integer-flag",
        "boolean-exit-code",
        "unknown-kind",
        "empty-repository",
        "extra-field",
        "non-text-gap",
        "duplicate-key",
        "non-canonical-time",
        "non-canonical-spacing",
        "bad-time",
        "event-outside-window",
        "array",
        "not-json",
        "nan",
        "deep-nesting",
    ],
)
def test_decoder_rejects_any_other_document(document: str) -> None:
    """Alternative encodings and invalid evidence never decode into an input."""
    with pytest.raises(SnapshotError):
        decode_input(document)


def test_any_field_change_changes_the_document() -> None:
    """Every report-relevant field is part of the hashed document."""
    base = lab_like()
    variants = [
        replace(base, declared_complete=True),
        replace(base, declared_truncated=False),
        replace(base, gaps=base.gaps[:1]),
        replace(base, truncated_observation_ids=()),
        replace(base, truncated_gap_count=0),
        replace(base, redaction_count=3),
        replace(base, history_repository=None),
        replace(base, observations=base.observations[:1]),
    ]
    documents = {encode_input(item) for item in variants}
    assert len(documents) == len(variants)
    assert encode_input(base) not in documents


def test_timestamp_overflow_is_a_safe_snapshot_error() -> None:
    """UTC conversion at the datetime boundary must not escape as a traceback."""
    payload = json.loads(encode_input(lab_like()))
    payload["incident"]["starts_at"] = "0001-01-01T00:00:00+14:00"
    document = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    with pytest.raises(SnapshotError, match="valid evidence snapshot"):
        decode_input(document)


@pytest.mark.parametrize("field", ["message", "gap", "history"])
def test_unicode_surrogates_are_refused_in_stored_text(field: str) -> None:
    """A JSON escape cannot introduce invalid Unicode into an otherwise valid input."""
    payload = json.loads(encode_input(lab_like()))
    invalid = "fictional-\ud800"
    if field == "message":
        payload["observations"][1]["message"] = invalid
    elif field == "gap":
        payload["collection"]["gaps"][0] = invalid
    else:
        payload["history_repository"] = invalid
    document = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    with pytest.raises(SnapshotError, match="Unicode"):
        decode_input(document)
