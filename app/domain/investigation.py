"""Apply two explicit offline rules without interpreting or executing messages."""

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
    for observation in snapshot.timeline:
        evidence = (EvidenceReference(observation.observation_id),)
        if observation.kind is ObservationKind.CONNECTION_REFUSED:
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
    return Investigation(
        incident=incident,
        sources=snapshot.sources,
        timeline=snapshot.timeline,
        facts=tuple(facts),
        hypotheses=tuple(hypotheses),
        contradictions=contradictions,
    )
