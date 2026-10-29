"""Apply explicit offline rules without interpreting or executing messages."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from app.domain.models import (
    Contradiction,
    EvidenceReference,
    Fact,
    Hypothesis,
    Incident,
    Investigation,
    Observation,
    ObservationKind,
    Source,
)

# These kinds report a failure symptom; an exit counts only when nonzero.
_FAILURE_KINDS = frozenset(
    {
        ObservationKind.CONNECTION_REFUSED,
        ObservationKind.METRIC_SIGNAL,
        ObservationKind.TRACE_ERROR,
    }
)
_SIGNAL_FACTS = {
    ObservationKind.METRIC_SIGNAL: (
        "metric_threshold_exceeded",
        "reports a metric above its allowlisted threshold.",
    ),
    ObservationKind.TRACE_ERROR: (
        "trace_error_recorded",
        "reports a span that ended with an error status.",
    ),
}
MAX_TRACE_EVIDENCE = 10


def _is_failure(observation: Observation, disputed: set[tuple[str, datetime]]) -> bool:
    """Treat failure symptoms or an undisputed nonzero exit as a failure signal."""
    if observation.kind in _FAILURE_KINDS:
        return True
    return (
        observation.kind is ObservationKind.CONTAINER_EXIT
        and observation.exit_code != 0
        and (observation.source_id, observation.event_time) not in disputed
    )


def _deployment_hypothesis(deployment: Observation, failure: Observation) -> Hypothesis:
    """Link a deployment to a later failure while stating that order is not cause."""
    return Hypothesis(
        code="deployment_preceded_failure",
        statement=(
            f"Deployment observation {deployment.observation_id} precedes "
            f"failure observation {failure.observation_id}. Temporal order alone "
            "does not establish that the deployment caused the failure."
        ),
        evidence=(
            EvidenceReference(deployment.observation_id),
            EvidenceReference(failure.observation_id),
        ),
    )


def _trace_facts(timeline: tuple[Observation, ...]) -> list[Fact]:
    """Link observations from different sources that carry the same trace ID."""
    traces: dict[str, list[Observation]] = defaultdict(list)
    for observation in timeline:
        if observation.trace_id is not None:
            traces[observation.trace_id].append(observation)
    facts: list[Fact] = []
    for trace_id, events in sorted(traces.items()):
        if len({event.source_id for event in events}) < 2:
            continue
        cited = events[:MAX_TRACE_EVIDENCE]
        facts.append(
            Fact(
                code="same_trace",
                statement=(
                    f"Observations {', '.join(e.observation_id for e in cited)} "
                    f"share trace {trace_id}; they describe the same request. "
                    "Agreement between sources does not establish a cause."
                ),
                evidence=tuple(EvidenceReference(e.observation_id) for e in cited),
            )
        )
    return facts


def investigate(
    incident: Incident,
    sources: Iterable[Source],
    observations: Iterable[Observation],
) -> Investigation:
    """Validate one bounded snapshot and derive reproducible findings with evidence."""
    snapshot = Investigation(incident, tuple(sources), tuple(observations))
    exits: dict[tuple[str, datetime], list[Observation]] = defaultdict(list)
    for observation in snapshot.timeline:
        if observation.kind is ObservationKind.CONTAINER_EXIT:
            exits[(observation.source_id, observation.event_time)].append(observation)
    disputed = {
        key for key, events in exits.items() if len({e.exit_code for e in events}) > 1
    }
    contradictions = tuple(
        Contradiction(
            code="conflicting_container_exit_codes",
            statement=(
                f"Source {source_id} reports different container exit codes at "
                f"{event_time.isoformat()}; the outcome is unresolved."
            ),
            evidence=tuple(EvidenceReference(event.observation_id) for event in events),
        )
        for (source_id, event_time), events in sorted(exits.items())
        if (source_id, event_time) in disputed
    )
    facts: list[Fact] = []
    hypotheses: list[Hypothesis] = []
    # Only the latest recorded deployment before a failure signal is linked to it.
    pending_deployment: Observation | None = None
    for observation in snapshot.timeline:
        evidence = (EvidenceReference(observation.observation_id),)
        if _is_failure(observation, disputed) and pending_deployment is not None:
            if pending_deployment.event_time < observation.event_time:
                hypotheses.append(
                    _deployment_hypothesis(pending_deployment, observation)
                )
                pending_deployment = None
        if observation.kind is ObservationKind.DEPLOYMENT:
            facts.append(
                Fact(
                    code="deployment_recorded",
                    statement=(
                        f"Observation {observation.observation_id} "
                        "records a deployment."
                    ),
                    evidence=evidence,
                )
            )
            pending_deployment = observation
        elif observation.kind in _SIGNAL_FACTS:
            code, text = _SIGNAL_FACTS[observation.kind]
            facts.append(
                Fact(
                    code=code,
                    statement=f"Observation {observation.observation_id} {text}",
                    evidence=evidence,
                )
            )
        elif observation.kind is ObservationKind.CONNECTION_REFUSED:
            facts.append(
                Fact(
                    code="connection_refused",
                    statement=(
                        f"Observation {observation.observation_id} "
                        "reports a refused connection."
                    ),
                    evidence=evidence,
                )
            )
            hypotheses.append(
                Hypothesis(
                    code="endpoint_may_be_unavailable",
                    statement=(
                        "A dependency endpoint may have been unavailable. "
                        "A refused connection alone does not establish the cause."
                    ),
                    evidence=evidence,
                )
            )
        elif observation.kind is ObservationKind.CONTAINER_EXIT:
            failed = observation.exit_code != 0
            facts.append(
                Fact(
                    code="container_exit_error" if failed else "container_exit_zero",
                    statement=(
                        f"Observation {observation.observation_id} reports container "
                        f"exit code {observation.exit_code}."
                    ),
                    evidence=evidence,
                )
            )
            if (
                failed
                and (observation.source_id, observation.event_time) not in disputed
            ):
                hypotheses.append(
                    Hypothesis(
                        code="application_failure_possible",
                        statement=(
                            "An application failure may have contributed. "
                            "A nonzero exit code alone does not establish the cause."
                        ),
                        evidence=evidence,
                    )
                )
    facts += _trace_facts(snapshot.timeline)
    return Investigation(
        incident=incident,
        sources=snapshot.sources,
        timeline=snapshot.timeline,
        facts=tuple(facts),
        hypotheses=tuple(hypotheses),
        contradictions=contradictions,
    )
