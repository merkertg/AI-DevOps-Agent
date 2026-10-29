"""Decide with fixed rules from tool results only; no model is called.

The provider sees the investigation only through tool results in its turn.
It reads structured fields such as fact codes and IDs and never interprets
observation messages, so log text cannot steer it.
"""

from typing import cast

from app.agent.contracts import AgentTurn, ToolExchange

# Tools are read in this order before answering.
_PLAN = (
    "get_overview",
    "list_sources",
    "list_facts",
    "list_rule_findings",
    "get_data_quality",
)
_FAILURE_FACTS = ("connection_refused", "container_exit_error")
_STATEMENTS = {
    "connection_refused": (
        "The dependency endpoint may have been unreachable when the service "
        "connected; the refusal alone does not show why."
    ),
    "container_exit_error": (
        "The service process exited with an error; the exit code alone does not "
        "identify the failing component."
    ),
}


def _data(exchanges: tuple[ToolExchange, ...], tool: str) -> dict[str, object] | None:
    """Return the latest successful result of one tool, if any."""
    for exchange in reversed(exchanges):
        if exchange.tool == tool and exchange.result.get("status") == "ok":
            return cast(dict[str, object], exchange.result["data"])
    return None


def _items(data: dict[str, object] | None) -> list[dict[str, object]]:
    """Return the item list of a successful paged or listed result."""
    if data is None:
        return []
    return cast(list[dict[str, object]], data.get("items", []))


class OfflineRulesProvider:
    """Read a fixed set of tools, then answer from structured fact codes."""

    name = "offline-rules-v1"

    def decide(self, turn: AgentTurn) -> object:
        """Call the next unread tool, or finish when the plan or budget is done."""
        attempted = {exchange.tool for exchange in turn.exchanges}
        if turn.remaining_tool_calls > 0 and turn.remaining_steps > 0:
            for tool in _PLAN:
                if tool not in attempted:
                    arguments: dict[str, object] = (
                        {"limit": 20}
                        if tool in {"list_facts", "list_rule_findings"}
                        else {}
                    )
                    return {"action": "call_tool", "tool": tool, "arguments": arguments}
        return self._answer(turn.exchanges)

    def _answer(self, exchanges: tuple[ToolExchange, ...]) -> dict[str, object]:
        """Build hypotheses and one next check from fact codes and IDs."""
        facts = _items(_data(exchanges, "list_facts"))
        rules = _items(_data(exchanges, "list_rule_findings"))
        sources = _items(_data(exchanges, "list_sources"))
        quality = _data(exchanges, "get_data_quality") or {}
        by_observation: dict[str, str] = {}
        for fact in facts:
            for observation_id in cast(list[str], fact["observation_ids"]):
                by_observation.setdefault(observation_id, str(fact["id"]))
        hypotheses: list[dict[str, object]] = []
        deployment_check: str | None = None
        for rule in rules:
            if rule["code"] != "deployment_preceded_failure":
                continue
            observations = cast(list[str], rule["observation_ids"])
            cited = [
                by_observation[item] for item in observations if item in by_observation
            ]
            if len(cited) == len(observations):
                hypotheses.append(
                    {
                        "statement": (
                            "The change deployed shortly before the failure may be "
                            "related to it; the order of events alone is not proof."
                        ),
                        "fact_ids": cited,
                        "observation_ids": observations,
                    }
                )
                deployment_check = deployment_check or observations[0]
        refusal: str | None = None
        for code in _FAILURE_FACTS:
            matching = [fact for fact in facts if fact["code"] == code]
            if not matching:
                continue
            observations = [
                item
                for fact in matching
                for item in cast(list[str], fact["observation_ids"])
            ]
            hypotheses.append(
                {
                    "statement": _STATEMENTS[code],
                    "fact_ids": [str(fact["id"]) for fact in matching],
                    "observation_ids": observations,
                }
            )
            if code == "connection_refused":
                refusal = observations[0]
        missing = cast(list[str], quality.get("sources_without_observations", []))
        logs = [item for item in sources if item["kind"] == "application_log"]
        check: dict[str, object] | None
        if deployment_check is not None:
            check = {
                "kind": "compare_deployed_revision",
                "target": deployment_check,
                "reason": "A recorded deployment precedes the first failure signal.",
            }
        elif refusal is not None:
            check = {
                "kind": "review_dependency_endpoint",
                "target": refusal,
                "reason": "A structured connection refusal is the earliest failure.",
            }
        elif missing:
            check = {
                "kind": "collect_missing_source",
                "target": missing[0],
                "reason": "A declared source has no observations.",
            }
        elif logs:
            check = {
                "kind": "widen_log_window",
                "target": str(logs[0]["source_id"]),
                "reason": "No failure signal was found in the current window.",
            }
        else:
            check = None
        return {
            "action": "finish",
            "hypotheses": hypotheses[:5],
            "next_check": check,
            "notes": [],
        }
