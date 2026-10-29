"""Check deployment evidence rules: source compatibility, facts and hypotheses."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain import (
    Incident,
    Investigation,
    Observation,
    ObservationKind,
    Source,
    SourceKind,
    investigate,
)

START = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
LOG = Source("api-log", "training-api", SourceKind.APPLICATION_LOG)
STATE = Source("api-state", "training-api", SourceKind.CONTAINER_STATE)
HISTORY = Source("lab-deployments", "training-api", SourceKind.DEPLOYMENT_HISTORY)
SOURCES = (LOG, STATE, HISTORY)


def incident() -> Incident:
    """Provide a fixed thirty-minute fictional investigation window."""
    return Incident("deploy-test", "training-api", START, START + timedelta(minutes=30))


def at(minutes: int) -> datetime:
    """Return a time inside the window, minutes after its start."""
    return START + timedelta(minutes=minutes)


def deployment(observation_id: str, minutes: int) -> Observation:
    """Create a recorded deployment observation from the history source."""
    return Observation(
        observation_id,
        HISTORY.source_id,
        at(minutes),
        at(minutes),
        ObservationKind.DEPLOYMENT,
    )


def refusal(observation_id: str, minutes: int) -> Observation:
    """Create a structured connection refusal from the log source."""
    return Observation(
        observation_id,
        LOG.source_id,
        at(minutes),
        at(minutes),
        ObservationKind.CONNECTION_REFUSED,
    )


def exited(observation_id: str, minutes: int, exit_code: int) -> Observation:
    """Create a container exit from the state source."""
    return Observation(
        observation_id,
        STATE.source_id,
        at(minutes),
        at(minutes),
        ObservationKind.CONTAINER_EXIT,
        exit_code=exit_code,
    )


def linked(observations: tuple[Observation, ...]) -> list[tuple[str, ...]]:
    """List the evidence of every deployment hypothesis in report order."""
    result = investigate(incident(), SOURCES, observations)
    return [
        tuple(reference.observation_id for reference in hypothesis.evidence)
        for hypothesis in result.hypotheses
        if hypothesis.code == "deployment_preceded_failure"
    ]


@pytest.mark.parametrize("source", [LOG, STATE])
def test_deployment_is_accepted_only_from_deployment_history(source: Source) -> None:
    """A log line or container state cannot claim to be a deployment record."""
    event = Observation(
        "deploy-1", source.source_id, at(1), at(1), ObservationKind.DEPLOYMENT
    )
    with pytest.raises(ValueError, match="incompatible"):
        Investigation(incident(), SOURCES, (event,))


@pytest.mark.parametrize(
    "kind", [ObservationKind.CONNECTION_REFUSED, ObservationKind.CONTAINER_EXIT]
)
def test_deployment_history_cannot_report_failure_signals(
    kind: ObservationKind,
) -> None:
    """History records deployments and notes, never refusals or exits."""
    event = Observation(
        "history-1",
        HISTORY.source_id,
        at(1),
        at(1),
        kind,
        exit_code=1 if kind is ObservationKind.CONTAINER_EXIT else None,
    )
    with pytest.raises(ValueError, match="incompatible"):
        Investigation(incident(), SOURCES, (event,))


def test_deployment_history_accepts_notes() -> None:
    """A history summary note is ordinary inert evidence."""
    note = Observation(
        "history-note", HISTORY.source_id, at(30), at(30), ObservationKind.NOTE
    )
    assert investigate(incident(), SOURCES, (note,)).facts == ()


def test_deployment_before_failure_is_a_hypothesis_not_a_fact() -> None:
    """Order produces a cited hypothesis whose statement denies proven causation."""
    result = investigate(
        incident(), SOURCES, (deployment("deploy-1", 5), refusal("refusal-1", 10))
    )
    assert [fact.code for fact in result.facts] == [
        "deployment_recorded",
        "connection_refused",
    ]
    hypothesis = next(
        item for item in result.hypotheses if item.code == "deployment_preceded_failure"
    )
    assert [ref.observation_id for ref in hypothesis.evidence] == [
        "deploy-1",
        "refusal-1",
    ]
    assert "does not establish" in hypothesis.statement


def test_only_the_latest_deployment_before_a_failure_is_linked() -> None:
    """Earlier deployments are recorded as facts without competing hypotheses."""
    events = (
        deployment("deploy-1", 2),
        deployment("deploy-2", 5),
        refusal("refusal-1", 10),
        refusal("refusal-2", 12),
    )
    assert linked(events) == [("deploy-2", "refusal-1")]


def test_each_deployment_links_to_the_next_failure_after_it() -> None:
    """A second deployment after a failure links to the following failure."""
    events = (
        deployment("deploy-1", 2),
        refusal("refusal-1", 5),
        deployment("deploy-2", 8),
        exited("exit-1", 9, 3),
    )
    assert linked(events) == [("deploy-1", "refusal-1"), ("deploy-2", "exit-1")]


@pytest.mark.parametrize(
    "events",
    [
        (refusal("refusal-1", 5), deployment("deploy-1", 10)),
        (deployment("deploy-1", 5), refusal("refusal-1", 5)),
        (deployment("deploy-1", 5), exited("exit-1", 10, 0)),
        (deployment("deploy-1", 5),),
    ],
    ids=["failure-first", "same-time", "zero-exit", "no-failure"],
)
def test_no_deployment_hypothesis_without_a_strictly_later_failure(
    events: tuple[Observation, ...],
) -> None:
    """Simultaneous, earlier or successful outcomes are not linked to a deploy."""
    assert linked(events) == []


def test_same_time_failure_does_not_block_a_later_link() -> None:
    """An ambiguous simultaneous failure leaves the deployment for the next one."""
    events = (
        deployment("deploy-1", 5),
        refusal("refusal-1", 5),
        refusal("refusal-2", 7),
    )
    assert linked(events) == [("deploy-1", "refusal-2")]


def test_disputed_exit_is_not_treated_as_a_failure() -> None:
    """Conflicting exit codes at one time stay unresolved, not linked to a deploy."""
    events = (
        deployment("deploy-1", 5),
        exited("exit-a", 10, 0),
        exited("exit-b", 10, 3),
    )
    result = investigate(incident(), SOURCES, events)
    assert linked(events) == []
    assert [item.code for item in result.contradictions] == [
        "conflicting_container_exit_codes"
    ]
