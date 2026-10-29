"""Validate a provider's final answer against the investigation it was given."""

from dataclasses import dataclass
from typing import Literal, cast

from app.agent.contracts import AgentLimits, FinalAnswer, ProposedCheck
from app.agent.tools import InvestigationView
from app.domain import ObservationKind, SourceKind
from app.infrastructure.redaction import sanitize_text


@dataclass(frozen=True, slots=True)
class CheckKind:
    """Describe one diagnostic check an engineer may choose to run."""

    description: str
    target: Literal["observation", "source", "missing_source"]
    kinds: frozenset[str]


# Proposed checks are suggestions for an engineer. Nothing in the project runs
# them, and no check changes a container, deployment or repository.
CHECKS: dict[str, CheckKind] = {
    "review_dependency_endpoint": CheckKind(
        "Confirm whether the dependency endpoint was reachable at the time of the "
        "cited refused connection.",
        "observation",
        frozenset({ObservationKind.CONNECTION_REFUSED.value}),
    ),
    "compare_deployed_revision": CheckKind(
        "Review what changed in the revision recorded by the cited deployment.",
        "observation",
        frozenset({ObservationKind.DEPLOYMENT.value}),
    ),
    "review_trace": CheckKind(
        "Open the cited failed span's trace in the tracing backend and review "
        "where the request failed.",
        "observation",
        frozenset({ObservationKind.TRACE_ERROR.value}),
    ),
    "inspect_container_state": CheckKind(
        "Read the current state and recent exits of the cited container source.",
        "source",
        frozenset({SourceKind.CONTAINER_STATE.value}),
    ),
    "widen_log_window": CheckKind(
        "Collect logs from the cited source for a wider window around the incident.",
        "source",
        frozenset({SourceKind.APPLICATION_LOG.value}),
    ),
    "collect_missing_source": CheckKind(
        "Collect evidence for the cited declared source, which has no observations.",
        "missing_source",
        frozenset(kind.value for kind in SourceKind),
    ),
}


def _target_is_valid(
    check: ProposedCheck, spec: CheckKind, view: InvestigationView
) -> bool:
    """Require an existing target of the kind the check applies to."""
    if spec.target == "observation":
        event = view.observations.get(check.target)
        return event is not None and event.kind.value in spec.kinds
    source = view.sources.get(check.target)
    if source is None or source.kind.value not in spec.kinds:
        return False
    if spec.target == "missing_source":
        quality = cast(dict[str, object], view.report["data_quality"])
        return check.target in cast(list[str], quality["sources_without_observations"])
    return True


def validate_answer(
    answer: FinalAnswer | None,
    view: InvestigationView,
    limits: AgentLimits,
    stop_reason: str | None,
) -> dict[str, object]:
    """Keep only hypotheses citing existing facts and record missing knowledge."""
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    redactions = 0

    def clean(text: str) -> str:
        """Redact and bound model text; it is shown to people as inert data."""
        nonlocal redactions
        cleaned, count, _ = sanitize_text(text, limits.max_text_chars)
        redactions += count
        return cleaned

    hypotheses = answer.hypotheses if answer is not None else ()
    for index, proposed in enumerate(hypotheses, start=1):
        reasons: set[str] = set()
        if not proposed.fact_ids:
            reasons.add("no_fact_reference")
        if any(item not in view.facts for item in proposed.fact_ids):
            reasons.add("unknown_fact_reference")
        if any(item not in view.observations for item in proposed.observation_ids):
            reasons.add("unknown_observation_reference")
        if reasons:
            rejected.append(
                {
                    "index": index,
                    "statement": clean(proposed.statement),
                    "reasons": sorted(reasons),
                }
            )
            continue
        accepted.append(
            {
                "hypothesis_id": f"agent-hypothesis-{len(accepted) + 1:03d}",
                "status": "unconfirmed",
                "statement": clean(proposed.statement),
                "fact_ids": list(proposed.fact_ids),
                "observation_ids": list(proposed.observation_ids),
            }
        )

    next_check: dict[str, object] | None = None
    rejected_check: dict[str, object] | None = None
    proposed_check = answer.next_check if answer is not None else None
    if proposed_check is not None:
        spec = CHECKS.get(proposed_check.kind)
        if spec is None:
            rejected_check = {"reasons": ["unknown_check_kind"]}
        elif not _target_is_valid(proposed_check, spec, view):
            rejected_check = {"reasons": ["invalid_check_target"]}
        else:
            next_check = {
                "kind": proposed_check.kind,
                "target": proposed_check.target,
                "description": spec.description,
                "reason": clean(proposed_check.reason),
                "status": "proposed",
                "executed": False,
            }

    quality = cast(dict[str, object], view.report["data_quality"])
    status = quality["status"]
    gaps = cast(list[str], quality["gaps"])
    insufficient: list[str] = []
    if stop_reason is not None:
        insufficient.append(
            f"The agent stopped at its {stop_reason.replace('_', ' ')} "
            "before a validated answer."
        )
    if status != "declared_complete":
        insufficient.append(
            f"Evidence quality is {status}; findings may change with more data."
        )
    if gaps:
        insufficient.append(f"{len(gaps)} collection gaps remain in the evidence.")
    if not view.facts:
        insufficient.append("No deterministic facts are available to cite.")
    if not accepted:
        insufficient.append("No proposed hypothesis is supported by existing facts.")
    if next_check is None:
        insufficient.append("No valid next diagnostic check was proposed.")
    notes = answer.notes if answer is not None else ()
    return {
        "hypotheses": accepted,
        "rejected_hypotheses": rejected,
        "next_check": next_check,
        "rejected_check": rejected_check,
        "insufficient_data": insufficient,
        "model_notes": [clean(note) for note in notes],
        "redaction_count": redactions,
    }
