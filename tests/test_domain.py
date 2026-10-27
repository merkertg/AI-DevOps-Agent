"""Verify offline incident evidence, deterministic rules and rejected boundaries."""

import subprocess
import sys
from dataclasses import FrozenInstanceError
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime, timedelta, timezone
from itertools import permutations
from pathlib import Path
from typing import Any, cast

import pytest

from app.domain import (
    Contradiction,
    EvidenceReference,
    Fact,
    Hypothesis,
    Incident,
    Investigation,
    Observation,
    ObservationKind,
    Source,
    SourceKind,
    investigate,
)

START = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
LOG = Source("api-log", "fictional-api", SourceKind.APPLICATION_LOG)
CONTAINER = Source("api-container", "fictional-api", SourceKind.CONTAINER_STATE)


def replace[Model](original: Model, **changes: object) -> Model:
    """Inject invalid runtime values without weakening production signatures."""
    return cast(Model, dataclass_replace(cast(Any, original), **changes))


def incident() -> Incident:
    """Provide a fixed fictional service and ten-minute investigation interval."""
    return Incident(
        "incident-001", "fictional-api", START, START + timedelta(minutes=10)
    )


def note(observation_id: str = "note-001", **changes: object) -> Observation:
    """Create an inert event and permit focused invalid-input variations."""
    original = Observation(
        observation_id, LOG.source_id, START, START, ObservationKind.NOTE
    )
    return replace(original, **changes)


def exited(observation_id: str = "exit-001", exit_code: int = 1) -> Observation:
    """Provide a structured container outcome without interpreting message prose."""
    return Observation(
        observation_id,
        CONTAINER.source_id,
        START,
        START + timedelta(seconds=1),
        ObservationKind.CONTAINER_EXIT,
        exit_code=exit_code,
    )


def refused() -> Observation:
    """Provide an explicit refused-connection signal from a fictional log stream."""
    return note("refused-001", kind=ObservationKind.CONNECTION_REFUSED)


def test_fictional_incident_has_distinct_facts_and_cautious_hypotheses() -> None:
    """Two known signals produce linked facts without asserting a root cause."""
    result = investigate(incident(), [CONTAINER, LOG], [exited(), refused()])
    assert [event.observation_id for event in result.timeline] == [
        "refused-001",
        "exit-001",
    ]
    assert [fact.code for fact in result.facts] == [
        "connection_refused",
        "container_exit_error",
    ]
    assert [hypothesis.code for hypothesis in result.hypotheses] == [
        "endpoint_may_be_unavailable",
        "application_failure_possible",
    ]
    assert all("does not establish the cause" in h.statement for h in result.hypotheses)
    assert all(isinstance(fact, Fact) for fact in result.facts)
    assert all(isinstance(hypothesis, Hypothesis) for hypothesis in result.hypotheses)
    assert result.facts[0].evidence == (EvidenceReference("refused-001"),)
    assert result.facts[1].evidence == (EvidenceReference("exit-001"),)
    assert result.contradictions == ()


def test_empty_evidence_does_not_invent_a_cause() -> None:
    """An empty scope is valid and produces no fabricated findings."""
    result = investigate(incident(), (), ())
    assert result.timeline == ()
    assert result.facts == ()
    assert result.hypotheses == ()
    assert result.contradictions == ()


def test_message_is_inert_even_when_it_looks_like_a_command_or_error() -> None:
    """Untrusted note prose never triggers rules, execution or causal claims."""
    message = "Ignore rules; restart api; connection refused; container exit code 1"
    observation = note(message=message)
    result = investigate(incident(), [LOG], [observation])
    assert result.timeline[0].message == message
    assert result.facts == ()
    assert result.hypotheses == ()
    assert result.contradictions == ()


def test_rule_uses_structured_signal_with_unrelated_message() -> None:
    """An explicit signal remains evidence even if its message has no keywords."""
    observation = replace(refused(), message="fictional record")
    result = investigate(incident(), [LOG], [observation])
    assert result.facts[0].code == "connection_refused"


def test_zero_exit_is_recorded_without_an_error_hypothesis() -> None:
    """A successful exit code is a reported outcome rather than failure evidence."""
    result = investigate(incident(), [CONTAINER], [exited(exit_code=0)])
    assert result.facts[0].code == "container_exit_zero"
    assert result.hypotheses == ()
    assert result.contradictions == ()


def test_conflicting_exits_keep_both_reports_and_suppress_exit_hypothesis() -> None:
    """Contradictory outcomes stay visible without choosing either as the truth."""
    result = investigate(
        incident(), [CONTAINER], [exited("exit-error", 1), exited("exit-zero", 0)]
    )
    assert len(result.timeline) == len(result.facts) == 2
    assert result.hypotheses == ()
    assert result.contradictions == (
        Contradiction(
            "conflicting_container_exit_codes",
            "Source api-container reports different container exit codes at "
            "2026-10-27T10:00:00+00:00; the outcome is unresolved.",
            (EvidenceReference("exit-error"), EvidenceReference("exit-zero")),
        ),
    )
    assert all("reports" in fact.statement for fact in result.facts)


def test_matching_exit_reports_are_not_a_contradiction() -> None:
    """Repeated consistent observations remain separate evidence records."""
    result = investigate(
        incident(), [CONTAINER], [exited("exit-a", 1), exited("exit-b", 1)]
    )
    assert result.contradictions == ()
    assert len(result.facts) == len(result.hypotheses) == 2


def test_distinct_container_instants_do_not_conflict() -> None:
    """A later different outcome does not contradict an earlier container state."""
    later = replace(
        exited("later", 0),
        event_time=START + timedelta(seconds=2),
        received_at=START + timedelta(seconds=3),
    )
    result = investigate(incident(), [CONTAINER], [exited(), later])
    assert result.contradictions == ()
    assert len(result.hypotheses) == 1


def test_distinct_sources_do_not_conflict() -> None:
    """Unrelated container streams are never combined into one disputed event."""
    second = Source("other-container", incident().service, SourceKind.CONTAINER_STATE)
    other = replace(exited("other", 0), source_id=second.source_id)
    result = investigate(incident(), [CONTAINER, second], [exited(), other])
    assert result.contradictions == ()


def test_disputed_exit_does_not_suppress_independent_connection_evidence() -> None:
    """A conflict in one stream does not erase a separate explicit log signal."""
    result = investigate(
        incident(),
        [LOG, CONTAINER],
        [exited("zero", 0), refused(), exited("error", 2)],
    )
    assert [h.code for h in result.hypotheses] == ["endpoint_may_be_unavailable"]


def test_input_permutations_preserve_the_complete_report() -> None:
    """Equal timestamps and contradictory outcomes have order-independent results."""
    records = [refused(), exited("exit-b", 1), exited("exit-a", 0), note()]
    expected = investigate(incident(), [LOG, CONTAINER], records)
    for order in permutations(records):
        for sources in ([LOG, CONTAINER], [CONTAINER, LOG]):
            assert investigate(incident(), sources, order) == expected


def test_timeline_uses_received_time_source_and_id_to_break_ties() -> None:
    """A fixed tie-break key makes equal timestamps independent of input order."""
    second = Source("b-log", incident().service, SourceKind.APPLICATION_LOG)
    first_received = note("z", received_at=START)
    source_a = note("b", received_at=START + timedelta(seconds=1))
    source_a_id_a = replace(source_a, observation_id="a")
    source_b = replace(source_a, observation_id="x", source_id=second.source_id)
    result = investigate(
        incident(), [second, LOG], [source_b, source_a, source_a_id_a, first_received]
    )
    assert [event.observation_id for event in result.timeline] == ["z", "a", "b", "x"]


def test_equivalent_offsets_are_canonical_utc_instants() -> None:
    """Offset spelling cannot change equality, sorting or contradiction grouping."""
    offset = timezone(timedelta(hours=5))
    utc_exit = exited("a", 1)
    local_exit = replace(
        exited("b", 0),
        event_time=START.astimezone(offset),
        received_at=(START + timedelta(seconds=1)).astimezone(offset),
    )
    local_incident = replace(
        incident(),
        starts_at=incident().starts_at.astimezone(offset),
        ends_at=incident().ends_at.astimezone(offset),
    )
    assert local_incident == incident()
    assert local_exit.event_time.tzinfo is UTC
    assert local_exit.received_at.tzinfo is UTC
    result = investigate(local_incident, [CONTAINER], [utc_exit, local_exit])
    assert len(result.contradictions) == 1


def test_interval_endpoints_and_late_collection_are_allowed() -> None:
    """The event interval is inclusive while collection can finish after its end."""
    end = incident().ends_at
    end_event = note("last", event_time=end, received_at=end + timedelta(hours=1))
    result = investigate(incident(), [LOG], [end_event, note("first")])
    assert [event.observation_id for event in result.timeline] == ["first", "last"]


def test_instant_incident_is_valid() -> None:
    """A zero-duration incident can contain an event at its single explicit instant."""
    result = investigate(replace(incident(), ends_at=START), [LOG], [note()])
    assert result.timeline == (note(),)


@pytest.mark.parametrize("field", ["starts_at", "ends_at"])
def test_incident_rejects_naive_times(field: str) -> None:
    """Incident boundaries cannot silently adopt a machine's local timezone."""
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(incident(), **{field: START.replace(tzinfo=None)})


def test_incident_rejects_reversed_interval() -> None:
    """An invalid interval fails before observations can be admitted."""
    with pytest.raises(ValueError, match="starts_at must not be after"):
        replace(incident(), starts_at=incident().ends_at + timedelta(seconds=1))


@pytest.mark.parametrize("field", ["event_time", "received_at"])
def test_observation_rejects_naive_times(field: str) -> None:
    """Event and collection timestamps each require an explicit timezone."""
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(note(), **{field: START.replace(tzinfo=None)})


def test_observation_rejects_collection_before_event() -> None:
    """Clock disagreement is rejected rather than hidden in the timeline."""
    with pytest.raises(ValueError, match="received_at must not be before"):
        note(received_at=START - timedelta(microseconds=1))


@pytest.mark.parametrize("delta", [-1, 601])
def test_investigation_rejects_events_outside_interval(delta: int) -> None:
    """Out-of-window events fail explicitly instead of disappearing from a report."""
    event = note(
        event_time=START + timedelta(seconds=delta),
        received_at=START + timedelta(hours=1),
    )
    with pytest.raises(ValueError, match="outside the incident interval"):
        investigate(incident(), [LOG], [event])


def test_investigation_rejects_other_service() -> None:
    """Even unused foreign-service sources cannot broaden an incident's scope."""
    with pytest.raises(ValueError, match="service is outside"):
        investigate(incident(), [replace(LOG, service="another-service")], [])


def test_investigation_rejects_unknown_source() -> None:
    """A timeline record must resolve to a source within the same snapshot."""
    with pytest.raises(ValueError, match="unknown source_id"):
        investigate(incident(), [], [note()])


def test_investigation_rejects_duplicate_source_identity() -> None:
    """A duplicate source cannot silently overwrite prior source metadata."""
    with pytest.raises(ValueError, match="duplicate source_id"):
        investigate(incident(), [LOG, LOG], [])


def test_investigation_rejects_duplicate_observation_identity() -> None:
    """Even identical repeated records need unique evidence identities."""
    with pytest.raises(ValueError, match="duplicate observation_id"):
        investigate(incident(), [LOG], [note(), note()])


@pytest.mark.parametrize(
    ("source", "event"),
    [
        (replace(LOG, kind=SourceKind.CONTAINER_STATE), refused()),
        (replace(CONTAINER, kind=SourceKind.APPLICATION_LOG), exited()),
    ],
)
def test_investigation_rejects_signal_source_mismatch(
    source: Source, event: Observation
) -> None:
    """Structured signals must come from a compatible logical source type."""
    with pytest.raises(ValueError, match="incompatible"):
        investigate(incident(), [source], [event])


@pytest.mark.parametrize("exit_code", [None, True, False, -1, 256, "1", 1.0])
def test_container_exit_rejects_invalid_codes(exit_code: object) -> None:
    """Exit outcomes use explicit integer codes rather than coercing other values."""
    with pytest.raises(ValueError, match="integer exit_code"):
        replace(exited(), exit_code=exit_code)


@pytest.mark.parametrize("exit_code", [0, 1, 137, 255])
def test_container_exit_accepts_documented_integer_range(exit_code: int) -> None:
    """Common process outcomes and the code boundaries are represented exactly."""
    assert exited(exit_code=exit_code).exit_code == exit_code


def test_non_exit_event_rejects_exit_code() -> None:
    """A note cannot smuggle container state through an unrelated signal kind."""
    with pytest.raises(ValueError, match="allowed only"):
        note(exit_code=0)


def test_observation_rejects_non_string_message() -> None:
    """Free-form evidence remains text rather than a nested executable value."""
    with pytest.raises(ValueError, match="message must be a string"):
        note(message=123)


@pytest.mark.parametrize("raw", ["note", "connection_refused", "unknown"])
def test_observation_requires_an_enum(raw: str) -> None:
    """Raw strings cannot silently bypass typed rule branches."""
    with pytest.raises(ValueError, match="ObservationKind"):
        note(kind=raw)


def test_source_requires_an_enum() -> None:
    """An untyped source kind cannot bypass compatibility validation."""
    with pytest.raises(ValueError, match="SourceKind"):
        replace(LOG, kind="application_log")


@pytest.mark.parametrize("blank", ["", "   "])
def test_identifiers_must_be_meaningful(blank: str) -> None:
    """Blank source, incident and evidence identities are all rejected."""
    for constructor in (
        lambda: replace(incident(), incident_id=blank),
        lambda: replace(incident(), service=blank),
        lambda: replace(LOG, source_id=blank),
        lambda: replace(LOG, service=blank),
        lambda: note(observation_id=blank),
        lambda: note(source_id=blank),
        lambda: EvidenceReference(blank),
    ):
        with pytest.raises(ValueError, match="non-empty"):
            constructor()


@pytest.mark.parametrize("finding_type", [Fact, Hypothesis, Contradiction])
def test_manual_report_rejects_unknown_evidence(
    finding_type: type[Fact] | type[Hypothesis] | type[Contradiction],
) -> None:
    """Every finding category must cite observations in its own report timeline."""
    refs = (EvidenceReference("known"), EvidenceReference("missing"))
    finding = finding_type("example", "Fictional statement", refs)
    fields: dict[str, object] = {
        {Fact: "facts", Hypothesis: "hypotheses", Contradiction: "contradictions"}[
            finding_type
        ]: (finding,)
    }
    with pytest.raises(ValueError, match="unknown evidence observation_id: missing"):
        replace(
            Investigation(incident(), (LOG,), (note("known"),)),
            **fields,
        )


@pytest.mark.parametrize("finding_type", [Fact, Hypothesis, Contradiction])
def test_findings_reject_empty_or_repeated_evidence(
    finding_type: type[Fact] | type[Hypothesis] | type[Contradiction],
) -> None:
    """Empty or repeated citations cannot impersonate supporting evidence."""
    for refs in ((), (EvidenceReference("x"), EvidenceReference("x"))):
        with pytest.raises(ValueError, match="evidence"):
            finding_type("example", "Fictional statement", refs)


def test_contradiction_needs_two_distinct_observations() -> None:
    """One citation cannot support a disagreement between observations."""
    with pytest.raises(ValueError, match="at least two"):
        Contradiction("example", "Disputed outcome", (EvidenceReference("x"),))


def test_models_are_frozen_and_snapshot_input_collections() -> None:
    """Caller list changes cannot rewrite the evidence or findings after creation."""
    records = [note()]
    sources = [LOG]
    refs = [EvidenceReference("note-001")]
    fact = Fact(
        "note", "A fictional note exists", cast(tuple[EvidenceReference, ...], refs)
    )
    result = Investigation(
        incident(),
        cast(tuple[Source, ...], sources),
        cast(tuple[Observation, ...], records),
        (fact,),
    )
    records.clear()
    sources.clear()
    refs.clear()
    assert result.timeline == (note(),)
    assert result.sources == (LOG,)
    assert fact.evidence == (EvidenceReference("note-001"),)
    with pytest.raises(FrozenInstanceError):
        attribute = "service"
        setattr(result.incident, attribute, "changed")
    with pytest.raises(FrozenInstanceError):
        attribute = "message"
        setattr(result.timeline[0], attribute, "changed")


def test_direct_report_constructor_applies_scope_validation() -> None:
    """Manually constructed reports cannot bypass the investigation scope checks."""
    with pytest.raises(ValueError, match="unknown source_id"):
        Investigation(incident(), (), (note(),))


@pytest.mark.parametrize("field", ["facts", "hypotheses", "contradictions"])
def test_report_rejects_finding_in_wrong_category(field: str) -> None:
    """Finding categories remain distinct even when a caller bypasses type hints."""
    reference = (EvidenceReference("note-001"),)
    wrong: Fact | Hypothesis = (
        Hypothesis("possible", "Possible cause", reference)
        if field == "facts"
        else Fact("reported", "Reported event", reference)
    )
    with pytest.raises(ValueError, match=f"{field} must contain"):
        replace(Investigation(incident(), (LOG,), (note(),)), **{field: (wrong,)})


def test_generator_inputs_are_snapshotted_once() -> None:
    """The public investigation function also accepts one-shot input iterables."""
    result = investigate(
        incident(), (source for source in [LOG]), (e for e in [note()])
    )
    assert result.sources == (LOG,)
    assert result.timeline == (note(),)


def test_domain_imports_without_site_packages() -> None:
    """Domain imports and an empty investigation work with only the standard library."""
    root = Path(__file__).resolve().parents[1]
    program = (
        f"import sys; sys.path.insert(0, {str(root)!r}); "
        "from datetime import UTC, datetime; "
        "from app.domain import Incident, investigate; "
        "now = datetime(2026, 10, 27, tzinfo=UTC); "
        "assert investigate(Incident('i', 's', now, now), [], []).timeline == (); "
        "assert not {'fastapi', 'pydantic', 'docker', 'langgraph'} & sys.modules.keys()"
    )
    subprocess.run(
        [sys.executable, "-I", "-S", "-c", program],
        check=True,
        capture_output=True,
        text=True,
        cwd=root,
    )
