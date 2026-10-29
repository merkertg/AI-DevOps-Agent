"""Validate final answers: existing references only, catalog checks, gaps shown."""

from typing import cast

import pytest

from app.agent.contracts import (
    AgentLimits,
    FinalAnswer,
    ProposedCheck,
    ProposedHypothesis,
)
from app.agent.tools import build_view
from app.agent.validation import CHECKS, validate_answer
from app.infrastructure.fixtures import load_fixture

from agent_support import DEPLOYMENT, ROOT, agent_view

LIMITS = AgentLimits()


def answer(
    *hypotheses: ProposedHypothesis, check: ProposedCheck | None = None
) -> FinalAnswer:
    """Wrap hypotheses and an optional check in a final answer."""
    return FinalAnswer(hypotheses, check, ())


def hypothesis(*facts: str, observations: tuple[str, ...] = ()) -> ProposedHypothesis:
    """Build a proposed hypothesis citing the given facts."""
    return ProposedHypothesis("Fictional statement.", facts, observations)


def test_hypothesis_citing_existing_facts_is_accepted_as_unconfirmed() -> None:
    """Accepted hypotheses keep their references and are never confirmed."""
    result = validate_answer(
        answer(hypothesis("fact-001", "fact-002", observations=(DEPLOYMENT,))),
        agent_view(),
        LIMITS,
        None,
    )
    accepted = cast(list[dict[str, object]], result["hypotheses"])
    assert accepted == [
        {
            "hypothesis_id": "agent-hypothesis-001",
            "status": "unconfirmed",
            "statement": "Fictional statement.",
            "fact_ids": ["fact-001", "fact-002"],
            "observation_ids": [DEPLOYMENT],
        }
    ]
    assert result["rejected_hypotheses"] == []


@pytest.mark.parametrize(
    ("proposed", "reasons"),
    [
        (hypothesis(), ["no_fact_reference"]),
        (hypothesis("fact-999"), ["unknown_fact_reference"]),
        (
            hypothesis("fact-001", observations=("obs-invented",)),
            ["unknown_observation_reference"],
        ),
        (
            hypothesis("rule-hypothesis-001"),
            ["unknown_fact_reference"],
        ),
        (
            hypothesis(observations=("obs-invented",)),
            ["no_fact_reference", "unknown_observation_reference"],
        ),
    ],
    ids=["no-facts", "unknown-fact", "unknown-observation", "rule-not-fact", "both"],
)
def test_unsupported_hypotheses_are_rejected_with_reasons(
    proposed: ProposedHypothesis, reasons: list[str]
) -> None:
    """Invented or missing references move a hypothesis to the rejected list."""
    result = validate_answer(answer(proposed), agent_view(), LIMITS, None)
    assert result["hypotheses"] == []
    assert result["rejected_hypotheses"] == [
        {"index": 1, "statement": "Fictional statement.", "reasons": reasons}
    ]
    assert "No proposed hypothesis is supported by existing facts." in cast(
        list[str], result["insufficient_data"]
    )


def test_model_text_is_redacted_and_bounded() -> None:
    """Statements, reasons and notes are filtered like evidence text."""
    limits = AgentLimits(max_text_chars=60)
    proposed = ProposedHypothesis(
        "Fictional token=fictional-model-secret " + "x" * 100, ("fact-001",), ()
    )
    check = ProposedCheck(
        "compare_deployed_revision", DEPLOYMENT, "Bearer fictional-bearer-0000"
    )
    result = validate_answer(
        FinalAnswer((proposed,), check, ("contact ops@example.invalid",)),
        agent_view(),
        limits,
        None,
    )
    text = str(result)
    for secret in ("fictional-model-secret", "fictional-bearer-0000", "ops@example"):
        assert secret not in text
    statement = cast(list[dict[str, str]], result["hypotheses"])[0]["statement"]
    assert len(statement) == 60
    assert result["redaction_count"] == 3


@pytest.mark.parametrize(
    ("check", "valid"),
    [
        (ProposedCheck("compare_deployed_revision", DEPLOYMENT, "r"), True),
        (ProposedCheck("review_dependency_endpoint", "obs-refusal", "r"), True),
        (ProposedCheck("inspect_container_state", "api-container", "r"), True),
        (ProposedCheck("widen_log_window", "api-log", "r"), True),
        (ProposedCheck("compare_deployed_revision", "obs-refusal", "r"), False),
        (ProposedCheck("widen_log_window", "api-container", "r"), False),
        (ProposedCheck("review_dependency_endpoint", "obs-invented", "r"), False),
        (ProposedCheck("collect_missing_source", "api-log", "r"), False),
    ],
)
def test_check_targets_must_exist_and_fit_the_check(
    check: ProposedCheck, valid: bool
) -> None:
    """A check applies only to an existing target of the matching kind."""
    result = validate_answer(answer(check=check), agent_view(), LIMITS, None)
    if valid:
        proposed = cast(dict[str, object], result["next_check"])
        assert proposed["status"] == "proposed"
        assert proposed["executed"] is False
        assert proposed["description"] == CHECKS[check.kind].description
    else:
        assert result["next_check"] is None
        assert result["rejected_check"] == {"reasons": ["invalid_check_target"]}


@pytest.mark.parametrize(
    "kind", ["restart_container", "rollback_deployment", "run_shell", "apply_fix"]
)
def test_actions_outside_the_check_catalog_are_rejected(kind: str) -> None:
    """Only diagnostic suggestions exist; repair actions are unknown kinds."""
    result = validate_answer(
        answer(check=ProposedCheck(kind, "api-container", "Fictional reason.")),
        agent_view(),
        LIMITS,
        None,
    )
    assert result["next_check"] is None
    assert result["rejected_check"] == {"reasons": ["unknown_check_kind"]}


def test_missing_source_check_needs_a_source_without_observations() -> None:
    """collect_missing_source applies only to declared sources lacking data."""
    view = build_view(load_fixture(ROOT / "fixtures/stage03/partial_incident.json"))
    result = validate_answer(
        answer(check=ProposedCheck("collect_missing_source", "api-container", "r")),
        view,
        LIMITS,
        None,
    )
    assert cast(dict[str, object], result["next_check"])["kind"] == (
        "collect_missing_source"
    )
    insufficient = cast(list[str], result["insufficient_data"])
    assert "Evidence quality is partial; findings may change with more data." in (
        insufficient
    )
    assert any("collection gaps remain" in item for item in insufficient)


def test_stopped_run_records_why_no_answer_exists() -> None:
    """Without an answer, the report lists the stop and the missing parts."""
    result = validate_answer(None, agent_view(), LIMITS, "time_limit")
    assert result["insufficient_data"] == [
        "The agent stopped at its time limit before a validated answer.",
        "No proposed hypothesis is supported by existing facts.",
        "No valid next diagnostic check was proposed.",
    ]
