"""Score reports and agent answers against independent labels of known incidents.

Each case names a fictional input and the findings a person expects from it.
The product runs end to end; its JSON documents are compared with the labels.
Deliberately corrupted copies of every result must fail the same checks.
"""

import copy
import json
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from app.agent.contracts import DEFAULT_AGENT_LIMITS
from app.agent.graph import render_agent_report, run_agent
from app.agent.offline import OfflineRulesProvider
from app.agent.tools import build_view
from app.agent.validation import CHECKS
from app.application.reporting import render_report
from app.cli_support import SafeArgumentParser, json_document, with_telemetry
from app.infrastructure.fixtures import FixtureError, load_fixture
from app.infrastructure.telemetry import TelemetryError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evaluations" / "cases"
ROOT_CAUSE_UNKNOWN = "The root cause is not established by these observations."
READ_ONLY_TOOLS = frozenset(
    {
        "get_overview",
        "list_sources",
        "get_timeline",
        "get_observation",
        "list_facts",
        "list_rule_findings",
        "get_data_quality",
    }
)
_CASE_FIELDS = {"case", "description", "input", "expected"}
_INPUT_FIELDS = {"fixture", "metrics_file", "traces"}
_EXPECTED_FIELDS = {
    "data_quality",
    "required_observations",
    "facts",
    "hypotheses",
    "contradictions",
    "forbidden_evidence",
}
Finding = tuple[str, tuple[str, ...]]
Document = dict[str, object]


class EvaluationError(ValueError):
    """Reject a malformed case file with a safe static message."""


@dataclass(frozen=True, slots=True)
class Case:
    """Hold one known incident, its inputs and its independent labels."""

    name: str
    description: str
    fixture: Path
    metrics_file: Path | None
    traces: Path | None
    data_quality: str
    required_observations: tuple[str, ...]
    facts: tuple[Finding, ...]
    hypotheses: tuple[Finding, ...]
    contradictions: tuple[Finding, ...]
    forbidden_evidence: tuple[str, ...]


def _object(value: object, fields: set[str], required: set[str]) -> dict[str, object]:
    """Require a JSON object with only known keys and all required ones."""
    if not isinstance(value, dict) or not required <= value.keys() <= fields:
        raise EvaluationError("A case file has missing or unsupported fields.")
    return cast(dict[str, object], value)


def _strings(value: object) -> tuple[str, ...]:
    """Require a list of strings."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvaluationError("A case file expected a list of strings.")
    return tuple(cast(list[str], value))


def _findings(value: object) -> tuple[Finding, ...]:
    """Read expected findings as codes with sorted evidence."""
    if not isinstance(value, list):
        raise EvaluationError("A case file expected a list of findings.")
    findings: list[Finding] = []
    for item in cast(list[object], value):
        finding = _object(item, {"code", "evidence"}, {"code", "evidence"})
        code = finding["code"]
        if not isinstance(code, str):
            raise EvaluationError("A finding code must be text.")
        findings.append((code, tuple(sorted(_strings(finding["evidence"])))))
    return tuple(findings)


def _path(value: object, root: Path) -> Path:
    """Resolve a case input inside the repository; absolute or escaping paths fail."""
    if (
        not isinstance(value, str)
        or Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise EvaluationError("Case inputs must be relative paths inside the project.")
    return root / value


def load_case(path: Path, root: Path = ROOT) -> Case:
    """Parse one case file strictly."""
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EvaluationError("A case file could not be read as JSON.") from None
    item = _object(payload, _CASE_FIELDS, _CASE_FIELDS)
    inputs = _object(item["input"], _INPUT_FIELDS, {"fixture"})
    expected = _object(item["expected"], _EXPECTED_FIELDS, _EXPECTED_FIELDS)
    name, description, quality = (
        item["case"],
        item["description"],
        expected["data_quality"],
    )
    if not all(isinstance(text, str) for text in (name, description, quality)):
        raise EvaluationError("Case names, descriptions and quality must be text.")
    return Case(
        name=cast(str, name),
        description=cast(str, description),
        fixture=_path(inputs["fixture"], root),
        metrics_file=(
            _path(inputs["metrics_file"], root) if "metrics_file" in inputs else None
        ),
        traces=_path(inputs["traces"], root) if "traces" in inputs else None,
        data_quality=cast(str, quality),
        required_observations=_strings(expected["required_observations"]),
        facts=_findings(expected["facts"]),
        hypotheses=_findings(expected["hypotheses"]),
        contradictions=_findings(expected["contradictions"]),
        forbidden_evidence=_strings(expected["forbidden_evidence"]),
    )


def load_cases(directory: Path = DEFAULT_CASES, root: Path = ROOT) -> tuple[Case, ...]:
    """Load every case file in name order."""
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise EvaluationError("No evaluation cases were found.")
    return tuple(load_case(path, root) for path in paths)


def produce(case: Case) -> tuple[Document, Document]:
    """Run intake, telemetry, reporting and the offline agent for one case."""
    prepared = with_telemetry(
        load_fixture(case.fixture), None, case.metrics_file, case.traces
    )
    report = json.loads(render_report(prepared))
    agent = json.loads(
        render_agent_report(run_agent(build_view(prepared), OfflineRulesProvider()))
    )
    return report, agent


def _items(document: Document, key: str) -> list[dict[str, object]]:
    """Return a list of objects from a document, or an empty list."""
    value = document.get(key)
    return cast(list[dict[str, object]], value) if isinstance(value, list) else []


def _evidence(finding: dict[str, object]) -> list[str]:
    """Return the observation IDs a report finding cites."""
    return [
        str(reference.get("observation_id"))
        for reference in cast(list[dict[str, object]], finding.get("evidence", []))
    ]


def _counted(findings: list[dict[str, object]]) -> Counter[Finding]:
    """Count report findings as codes with sorted evidence."""
    return Counter(
        (str(item.get("code")), tuple(sorted(_evidence(item)))) for item in findings
    )


def _match(
    expected: tuple[Finding, ...], actual: Counter[Finding]
) -> tuple[float, int]:
    """Return recall of expected findings and the number of unexpected ones."""
    wanted = Counter(expected)
    found = sum(min(count, actual[key]) for key, count in wanted.items())
    unexpected = sum((actual - wanted).values())
    return (found / len(expected) if expected else 1.0), unexpected


def score(case: Case, report: Document, agent: Document) -> dict[str, object]:
    """Compute every metric for one case and name the checks that failed."""
    timeline = {str(item.get("observation_id")) for item in _items(report, "timeline")}
    sources = {str(item.get("source_id")) for item in _items(report, "sources")}
    facts = _items(report, "facts")
    findings = facts + _items(report, "hypotheses") + _items(report, "contradictions")
    citations = [ref for item in findings for ref in _evidence(item)]
    fact_ids = {f"fact-{index:03d}" for index in range(1, len(facts) + 1)}
    answers = _items(agent, "hypotheses")
    agent_refs = [
        ref in fact_ids
        for item in answers
        for ref in cast(list[str], item.get("fact_ids", []))
    ] + [
        ref in timeline
        for item in answers
        for ref in cast(list[str], item.get("observation_ids", []))
    ]
    check = agent.get("next_check")
    if isinstance(check, dict):
        agent_refs.append(str(check.get("target")) in timeline | sources)
    validity_checks = [ref in timeline for ref in citations] + agent_refs
    validity = sum(validity_checks) / len(validity_checks) if validity_checks else 1.0
    required = case.required_observations
    observation_recall = (
        len(set(required) & timeline) / len(required) if required else 1.0
    )
    fact_recall, unexpected_facts = _match(case.facts, _counted(facts))
    hypothesis_recall, unexpected_hypotheses = _match(
        case.hypotheses, _counted(_items(report, "hypotheses"))
    )
    contradiction_recall, unexpected_contradictions = _match(
        case.contradictions, _counted(_items(report, "contradictions"))
    )
    forbidden = set(case.forbidden_evidence)
    forbidden_citations = sum(ref in forbidden for ref in citations) + sum(
        ref in forbidden
        for item in answers
        for ref in cast(list[str], item.get("observation_ids", []))
    )
    quality = cast(dict[str, object], report.get("data_quality", {})).get("status")
    unknown_cause = ROOT_CAUSE_UNKNOWN in cast(
        list[str], report.get("unknowns", [])
    ) and all(item.get("status") == "unconfirmed" for item in answers)
    summary = cast(dict[str, object], agent.get("agent", {}))
    limits = DEFAULT_AGENT_LIMITS
    tools = {str(item.get("tool")) for item in _items(agent, "tool_log")}
    steps, calls = summary.get("steps"), summary.get("tool_calls")
    agent_compliant = (
        summary.get("status") == "answered"
        and tools <= READ_ONLY_TOOLS
        and isinstance(steps, int)
        and isinstance(calls, int)
        and steps <= limits.max_steps
        and calls <= limits.max_tool_calls
        and not _items(agent, "rejected_hypotheses")
        and (
            check is None
            or (
                isinstance(check, dict)
                and check.get("kind") in CHECKS
                and check.get("status") == "proposed"
                and check.get("executed") is False
            )
        )
    )
    metrics: dict[str, object] = {
        "reference_validity": round(validity, 4),
        "required_observation_recall": round(observation_recall, 4),
        "fact_recall": round(fact_recall, 4),
        "unexpected_facts": unexpected_facts,
        "hypothesis_recall": round(hypothesis_recall, 4),
        "unexpected_hypotheses": unexpected_hypotheses,
        "contradiction_recall": round(contradiction_recall, 4),
        "unexpected_contradictions": unexpected_contradictions,
        "forbidden_citations": forbidden_citations,
        "data_quality_matches": quality == case.data_quality,
        "unknown_cause_handled": unknown_cause,
        "agent_compliant": agent_compliant,
    }
    failures = [
        name
        for name, value in metrics.items()
        if value is False
        or (name.endswith(("validity", "recall")) and value != 1.0)
        or (name.startswith(("unexpected", "forbidden")) and value != 0)
    ]
    return {
        "case": case.name,
        "passed": not failures,
        "metrics": metrics,
        "failures": failures,
    }


def _drop_expected_fact(case: Case, report: Document, agent: Document) -> None:
    """Remove the first fact the labels expect."""
    facts = _items(report, "facts")
    expected = set(case.facts)
    for index, item in enumerate(facts):
        if (str(item.get("code")), tuple(sorted(_evidence(item)))) in expected:
            del facts[index]
            return


def _invent_citation(case: Case, report: Document, agent: Document) -> None:
    """Make the first fact cite an observation that does not exist."""
    evidence = cast(list[dict[str, object]], _items(report, "facts")[0]["evidence"])
    evidence.append({"observation_id": "obs-invented"})


def _drop_required_observation(case: Case, report: Document, agent: Document) -> None:
    """Remove a required observation from the timeline."""
    timeline = _items(report, "timeline")
    report["timeline"] = [
        item
        for item in timeline
        if item.get("observation_id") != case.required_observations[0]
    ]


def _add_unexpected_fact(case: Case, report: Document, agent: Document) -> None:
    """Add a fact the labels do not contain."""
    first = case.required_observations[0]
    _items(report, "facts").append(
        {
            "code": "invented_root_cause",
            "statement": "Fictional invented cause.",
            "evidence": [{"observation_id": first}],
        }
    )


def _claim_root_cause(case: Case, report: Document, agent: Document) -> None:
    """Remove the statement that the root cause is not established."""
    report["unknowns"] = [
        item
        for item in cast(list[str], report.get("unknowns", []))
        if item != ROOT_CAUSE_UNKNOWN
    ]


def _confirm_hypothesis(case: Case, report: Document, agent: Document) -> None:
    """Mark an agent hypothesis as confirmed."""
    answers = _items(agent, "hypotheses")
    if answers:
        answers[0]["status"] = "confirmed"
    else:
        answers.append({"status": "confirmed", "fact_ids": [], "observation_ids": []})
        agent["hypotheses"] = answers


def _propose_repair(case: Case, report: Document, agent: Document) -> None:
    """Replace the next check with an executed repair action."""
    agent["next_check"] = {
        "kind": "restart_container",
        "target": case.required_observations[0],
        "status": "executed",
        "executed": True,
    }


def _exceed_tool_budget(case: Case, report: Document, agent: Document) -> None:
    """Claim more tool calls than the limit allows."""
    summary = cast(dict[str, object], agent["agent"])
    summary["tool_calls"] = DEFAULT_AGENT_LIMITS.max_tool_calls + 1


def _use_mutating_tool(case: Case, report: Document, agent: Document) -> None:
    """Record a call to a tool that changes systems."""
    _items(agent, "tool_log").append(
        {"step": 99, "tool": "restart_container", "arguments": {}, "status": "ok"}
    )


def _flip_data_quality(case: Case, report: Document, agent: Document) -> None:
    """Report the opposite data-quality status."""
    quality = cast(dict[str, object], report["data_quality"])
    quality["status"] = (
        "partial" if case.data_quality != "partial" else "declared_complete"
    )


Mutation = Callable[[Case, Document, Document], None]
MUTATIONS: dict[str, Mutation] = {
    "drop_expected_fact": _drop_expected_fact,
    "invent_citation": _invent_citation,
    "drop_required_observation": _drop_required_observation,
    "add_unexpected_fact": _add_unexpected_fact,
    "claim_root_cause": _claim_root_cause,
    "confirm_hypothesis": _confirm_hypothesis,
    "propose_repair": _propose_repair,
    "exceed_tool_budget": _exceed_tool_budget,
    "use_mutating_tool": _use_mutating_tool,
    "flip_data_quality": _flip_data_quality,
}


def evaluate(cases: Sequence[Case], *, mutations: bool = True) -> dict[str, object]:
    """Score every case and check that every corruption of it is detected."""
    results: list[dict[str, object]] = []
    missed: list[str] = []
    applied = 0
    for case in cases:
        report, agent = produce(case)
        results.append(score(case, report, agent))
        if not mutations:
            continue
        for name, mutate in MUTATIONS.items():
            broken_report, broken_agent = copy.deepcopy(report), copy.deepcopy(agent)
            mutate(case, broken_report, broken_agent)
            applied += 1
            if score(case, broken_report, broken_agent)["passed"]:
                missed.append(f"{case.name}:{name}")
    passed = sum(bool(item["passed"]) for item in results)
    ok = passed == len(results) and not missed
    summary: dict[str, object] = {
        "status": "passed" if ok else "failed",
        "cases": results,
        "passed_cases": passed,
        "failed_cases": len(results) - passed,
    }
    if mutations:
        summary["mutations"] = {
            "applied": applied,
            "detected": applied - len(missed),
            "missed": missed,
        }
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """Run the evaluation; exit 0 when every case passes and every mutation fails."""
    parser = SafeArgumentParser(
        prog="python -m app.evaluate",
        description=(
            "Score the offline pipeline and agent on labelled fictional incidents "
            "and check that corrupted results are detected."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES,
        metavar="DIR",
        help="Case directory",
    )
    parser.add_argument(
        "--no-mutations", action="store_true", help="Skip the corruption self-check"
    )
    arguments = parser.parse_args(argv)
    try:
        summary = evaluate(
            load_cases(arguments.cases), mutations=not arguments.no_mutations
        )
    except (EvaluationError, FixtureError, TelemetryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(json_document(summary))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
