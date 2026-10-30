"""Check the evaluation suite: labelled cases pass and corruptions are caught."""

import json
import shutil
from pathlib import Path

import pytest

from app.evaluate import (
    MUTATIONS,
    EvaluationError,
    evaluate,
    load_case,
    load_cases,
    main,
    produce,
    score,
)

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evaluations" / "cases"
# Each corruption must fail at least the named check.
EXPECTED_FAILURE = {
    "drop_expected_fact": "fact_recall",
    "invent_citation": "reference_validity",
    "drop_required_observation": "required_observation_recall",
    "add_unexpected_fact": "unexpected_facts",
    "claim_root_cause": "unknown_cause_handled",
    "confirm_hypothesis": "unknown_cause_handled",
    "propose_repair": "agent_compliant",
    "exceed_tool_budget": "agent_compliant",
    "use_mutating_tool": "agent_compliant",
    "flip_data_quality": "data_quality_matches",
}


def test_every_labelled_case_passes_with_perfect_metrics() -> None:
    """The offline pipeline meets every label of every committed case."""
    summary = evaluate(load_cases(CASES), mutations=False)
    assert summary["status"] == "passed"
    cases = summary["cases"]
    assert isinstance(cases, list) and len(cases) == 6
    for case in cases:
        metrics = case["metrics"]
        assert metrics["reference_validity"] == 1.0
        assert metrics["required_observation_recall"] == 1.0
        assert metrics["fact_recall"] == 1.0
        assert metrics["unexpected_facts"] == 0
        assert metrics["agent_compliant"] is True


@pytest.mark.parametrize(
    "case_path", sorted(CASES.glob("*.json")), ids=lambda p: p.stem
)
@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_each_corruption_fails_the_intended_check(
    case_path: Path, mutation: str
) -> None:
    """A deliberately broken report or agent answer never passes."""
    case = load_case(case_path)
    report, agent = produce(case)
    MUTATIONS[mutation](case, report, agent)
    result = score(case, report, agent)
    assert result["passed"] is False
    assert EXPECTED_FAILURE[mutation] in result["failures"]  # type: ignore[operator]


def test_suite_reports_all_mutations_detected() -> None:
    """The self-check counts applied and detected corruptions."""
    summary = evaluate(load_cases(CASES))
    assert summary["mutations"] == {
        "applied": 6 * len(MUTATIONS),
        "detected": 6 * len(MUTATIONS),
        "missed": [],
    }


def test_wrong_label_makes_the_case_fail(tmp_path: Path) -> None:
    """Labels are independent: a label the product does not meet is a failure."""
    data = json.loads((CASES / "connection-failure.json").read_text())
    data["expected"]["facts"][0]["evidence"] = ["obs-note"]
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(data))
    summary = evaluate([load_case(path)], mutations=False)
    assert summary["status"] == "failed"
    failures = summary["cases"][0]["failures"]  # type: ignore[index]
    assert "fact_recall" in failures and "unexpected_facts" in failures


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"input": {"fixture": "/etc/passwd"}},
        {"input": {"fixture": "../outside.json"}},
        {"input": {"fixture": "fixtures/stage03/connection_failure.json", "url": "x"}},
        {"expected": {"facts": []}},
    ],
)
def test_malformed_cases_are_rejected(
    change: dict[str, object], tmp_path: Path
) -> None:
    """Unknown fields, escaping paths and incomplete labels are refused."""
    data = json.loads((CASES / "connection-failure.json").read_text())
    data.update(change)
    path = tmp_path / "case.json"
    path.write_text(json.dumps(data))
    with pytest.raises(EvaluationError):
        load_case(path)


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Exit 0 when all pass, 1 when a label fails, 2 for unusable case files."""
    assert main(["--cases", str(CASES), "--no-mutations"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
    broken = tmp_path / "broken"
    broken.mkdir()
    data = json.loads((CASES / "partial-collection.json").read_text())
    data["expected"]["data_quality"] = "declared_complete"
    (broken / "case.json").write_text(json.dumps(data))
    assert main(["--cases", str(broken), "--no-mutations"]) == 1
    capsys.readouterr()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["--cases", str(empty)]) == 2
    assert "No evaluation cases" in capsys.readouterr().err
    shutil.rmtree(empty)
