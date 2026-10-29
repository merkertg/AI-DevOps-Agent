"""Check the agent's read-only tools: scope, paging, arguments and size limits."""

from typing import cast

import pytest

from app.agent.contracts import AgentLimits
from app.agent.tools import ToolBox

from agent_support import DEPLOYMENT, INJECTION, agent_view


def toolbox(limits: AgentLimits | None = None, note: str | None = None) -> ToolBox:
    """Bind tools to the fictional deployment investigation."""
    return ToolBox(agent_view(note), limits or AgentLimits())


def data(result: dict[str, object]) -> dict[str, object]:
    """Return the data of a successful tool result."""
    assert result["status"] == "ok", result
    return cast(dict[str, object], result["data"])


def items(result: dict[str, object]) -> list[dict[str, object]]:
    """Return the items of a successful listed result."""
    return cast(list[dict[str, object]], data(result)["items"])


def test_catalog_has_only_read_only_tools() -> None:
    """No tool can execute, restart, deploy, roll back or write."""
    names = {spec.name for spec in toolbox().specs}
    assert names == {
        "get_overview",
        "list_sources",
        "get_timeline",
        "get_observation",
        "list_facts",
        "list_rule_findings",
        "get_data_quality",
    }
    forbidden = ("shell", "exec", "restart", "deploy", "rollback", "write", "fix")
    assert not [name for name in names if any(word in name for word in forbidden)]


def test_overview_describes_only_the_current_investigation() -> None:
    """The overview is scoped to one incident and counts its evidence."""
    overview = data(toolbox().run("get_overview", {}))
    assert overview["incident_id"] == "training-after-deployment"
    assert overview["investigation_id"] == "inv-" + "0" * 24
    assert overview["deployment_history"] == "training-api-lab"
    assert overview["counts"] == {
        "sources": 3,
        "observations": 4,
        "facts": 3,
        "rule_findings": 3,
    }


def test_sources_list_counts_observations() -> None:
    """Each declared source appears with its observation count."""
    sources = {
        item["source_id"]: item for item in items(toolbox().run("list_sources", {}))
    }
    assert sources["training-api-lab-deployments"] == {
        "source_id": "training-api-lab-deployments",
        "kind": "deployment_history",
        "observations": 1,
    }


def test_facts_have_stable_ids_and_evidence() -> None:
    """Facts are numbered in report order with their observation IDs."""
    facts = items(toolbox().run("list_facts", {}))
    assert [(fact["id"], fact["code"]) for fact in facts] == [
        ("fact-001", "deployment_recorded"),
        ("fact-002", "connection_refused"),
        ("fact-003", "container_exit_error"),
    ]
    assert facts[0]["observation_ids"] == [DEPLOYMENT]


def test_rule_findings_include_the_deployment_hypothesis() -> None:
    """Deterministic rule hypotheses are visible with their own IDs."""
    findings = items(toolbox().run("list_rule_findings", {"limit": 20}))
    codes = [finding["code"] for finding in findings]
    assert "deployment_preceded_failure" in codes
    assert all(finding["type"] == "rule_hypothesis" for finding in findings)


def test_timeline_pages_filters_and_shortens_messages() -> None:
    """Pages are bounded; long messages are shortened and marked."""
    tools = toolbox(note="x" * 500)
    first = data(tools.run("get_timeline", {"limit": 2}))
    assert (first["total"], first["offset"], first["next_offset"]) == (5, 0, 2)
    logs = data(tools.run("get_timeline", {"source_id": "api-log", "limit": 20}))
    long_item = next(
        item
        for item in cast(list[dict[str, object]], logs["items"])
        if item["observation_id"] == "obs-injected"
    )
    assert long_item["message_truncated"] is True
    assert len(cast(str, long_item["message"])) == 240
    refusals = data(tools.run("get_timeline", {"kind": "connection_refused"}))
    assert refusals["total"] == 1 and refusals["next_offset"] is None
    full = data(tools.run("get_observation", {"observation_id": "obs-injected"}))
    assert full["message"] == "x" * 500


def test_injected_log_text_is_returned_as_data() -> None:
    """Text that looks like an instruction is an ordinary message field."""
    result = toolbox(note=INJECTION).run(
        "get_observation", {"observation_id": "obs-injected"}
    )
    assert data(result)["message"] == INJECTION
    assert data(result)["kind"] == "note"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_overview", {"verbose": True}),
        ("get_timeline", {"source_id": "other-service-log"}),
        ("get_timeline", {"source_id": 5}),
        ("get_timeline", {"kind": "restart"}),
        ("get_timeline", {"limit": 0}),
        ("get_timeline", {"limit": 21}),
        ("get_timeline", {"limit": True}),
        ("get_timeline", {"offset": -1}),
        ("get_timeline", {"offset": 100_001}),
        ("get_observation", {}),
        ("get_observation", {"observation_id": "obs-from-another-incident"}),
        ("get_observation", {"observation_id": ["obs-refusal"]}),
        ("list_facts", {"limit": "10"}),
        ("restart_container", {}),
        ("run_shell", {"command": "id"}),
        ("deploy_revision", {"revision": "d" * 40}),
    ],
)
def test_bad_arguments_and_unknown_tools_return_errors(
    tool: str, arguments: dict[str, object]
) -> None:
    """Refusals are results with static messages, never exceptions or echoes."""
    result = toolbox().run(tool, arguments)
    assert result["status"] == "error"
    assert "other-service" not in str(result) and "obs-from" not in str(result)


def test_oversized_results_are_refused() -> None:
    """A result larger than the limit is replaced by an error."""
    tools = toolbox(AgentLimits(max_tool_result_chars=200))
    result = tools.run("list_facts", {"limit": 20})
    assert result == {
        "status": "error",
        "error": "Tool result exceeds the size limit; request a smaller page.",
    }
    assert tools.run("get_data_quality", {})["status"] == "error"


def test_tools_do_not_change_the_view() -> None:
    """Reading tools many times leaves the frozen view identical."""
    view = agent_view()
    tools = ToolBox(view, AgentLimits())
    before = (dict(view.facts), dict(view.observations), dict(view.report))
    for spec in tools.specs:
        tools.run(
            spec.name,
            {"observation_id": "obs-refusal"} if spec.name == "get_observation" else {},
        )
    assert (dict(view.facts), dict(view.observations), dict(view.report)) == before
