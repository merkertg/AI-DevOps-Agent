"""Check that offline reports preserve sanitized evidence and visible uncertainty."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.inputs import PreparedInput
from app.application.reporting import build_report, render_report
from app.domain import Incident, Observation, ObservationKind, Source, SourceKind
from app.infrastructure.fixtures import load_fixture

ROOT = Path(__file__).resolve().parents[1]


def prepared() -> PreparedInput:
    """Supply one fictional signal with declared coverage and no external inputs."""
    instant = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    return PreparedInput(
        incident=Incident("report-test", "training-api", instant, instant),
        sources=(Source("api-log", "training-api", SourceKind.APPLICATION_LOG),),
        observations=(
            Observation(
                "obs-a", "api-log", instant, instant, ObservationKind.CONNECTION_REFUSED
            ),
        ),
        declared_complete=True,
    )


def test_report_links_findings_to_the_sanitized_timeline() -> None:
    """The documented fixture retains resolvable evidence while removing its email."""
    report = json.loads(
        render_report(load_fixture(ROOT / "fixtures/stage03/connection_failure.json"))
    )
    assert [row["observation_id"] for row in report["timeline"]] == [
        "obs-refusal",
        "obs-exit",
        "obs-note",
    ]
    assert len(report["facts"]) == len(report["hypotheses"]) == 2
    assert report["contradictions"] == []
    assert report["data_quality"]["status"] == "declared_complete"
    assert report["data_quality"]["redaction_count"] >= 1
    assert "learner@example.invalid" not in json.dumps(report)
    assert "restart the container" in report["timeline"][2]["message"]
    ids = {event["observation_id"] for event in report["timeline"]}
    for category in ("facts", "hypotheses", "contradictions"):
        for finding in report[category]:
            assert all(ref["observation_id"] in ids for ref in finding["evidence"])
    assert report["timeline"][0]["event_time"] == "2026-10-27T10:00:10+00:00"


def test_partial_fixture_keeps_collector_gaps_and_uncovered_sources_visible() -> None:
    """Missing container observations cannot look complete after successful parsing."""
    report = json.loads(
        render_report(load_fixture(ROOT / "fixtures/stage03/partial_incident.json"))
    )
    quality = report["data_quality"]
    assert quality["status"] == "partial"
    assert quality["declared_complete"] is False
    assert quality["declared_truncated"] is True
    assert quality["sources_without_observations"] == ["api-container"]
    assert quality["gaps"]
    assert "learner@example.invalid" not in json.dumps(report)
    assert any("root cause" in unknown for unknown in report["unknowns"])


@pytest.mark.parametrize(
    "value",
    [
        replace(prepared(), declared_complete=False),
        replace(prepared(), declared_truncated=True),
        replace(prepared(), gaps=("Fictional missing segment",)),
        replace(prepared(), truncated_observation_ids=("obs-a",)),
        replace(prepared(), truncated_gap_count=1),
        replace(prepared(), observations=()),
        replace(
            prepared(),
            sources=(
                *prepared().sources,
                Source("unused", "training-api", SourceKind.APPLICATION_LOG),
            ),
        ),
    ],
)
def test_each_incompleteness_signal_overrides_declared_complete(
    value: PreparedInput,
) -> None:
    """A producer's complete flag never conceals any known evidence-quality gap."""
    report = json.loads(render_report(value))
    assert report["data_quality"]["status"] == "partial"
    assert len(report["unknowns"]) > 1


def test_missing_declaration_is_unknown_instead_of_verified_complete() -> None:
    """A usable nonempty fixture without collection metadata keeps unknown coverage."""
    report = json.loads(render_report(replace(prepared(), declared_complete=None)))
    assert report["data_quality"]["status"] == "unknown"
    assert report["data_quality"]["declared_complete"] is None


def test_conflicts_degrade_quality_without_erasing_their_evidence() -> None:
    """Contradictory outcomes remain explicitly unresolved in the final CLI report."""
    value = prepared()
    instant = value.incident.starts_at
    source = Source("container", "training-api", SourceKind.CONTAINER_STATE)
    events = tuple(
        Observation(
            f"exit-{code}",
            "container",
            instant,
            instant,
            ObservationKind.CONTAINER_EXIT,
            exit_code=code,
        )
        for code in (0, 1)
    )
    report = json.loads(
        render_report(replace(value, sources=(source,), observations=events))
    )
    assert report["data_quality"]["status"] == "partial"
    assert len(report["timeline"]) == len(report["facts"]) == 2
    assert len(report["contradictions"]) == 1
    assert report["hypotheses"] == []


def test_json_is_deterministic_under_input_permutations_and_escapes_text() -> None:
    """Reordered evidence yields byte-identical JSON and cannot add stdout records."""
    value = prepared()
    second = replace(
        value.observations[0],
        observation_id="obs-b",
        message='\n{"fake": true}\nПример',
    )
    first = replace(value, observations=(*value.observations, second))
    reordered = replace(
        first,
        sources=tuple(reversed(first.sources)),
        observations=tuple(reversed(first.observations)),
    )
    output = render_report(first)
    assert output == render_report(reordered)
    assert output.isascii()
    assert output.endswith("\n")
    assert json.loads(output)["timeline"][1]["message"] == second.message
    assert build_report(value)["schema_version"] == 1
