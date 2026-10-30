"""Run the bounded investigation graph: decide, run a read-only tool, validate.

The provider only chooses among the listed read-only tools or finishes. Every
decision is parsed strictly, every limit is checked in code, and the final
answer is validated against the investigation before it is reported.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langsmith.run_helpers import tracing_context

from app.agent.contracts import (
    DEFAULT_AGENT_LIMITS,
    TOOL_NAME,
    AgentLimits,
    AgentProvider,
    AgentTurn,
    DecisionError,
    FinalAnswer,
    ToolCall,
    ToolExchange,
    parse_decision,
)
from app.agent.tools import InvestigationView, ToolBox
from app.agent.validation import CHECKS, validate_answer

AGENT_REPORT_VERSION = 1
StopReason = Literal["step_limit", "time_limit", "provider_error"]
_LIMITATIONS = (
    "Agent hypotheses are unconfirmed suggestions linked to deterministic facts; "
    "they do not establish a root cause.",
    "The proposed check is advice for an engineer; the agent cannot run commands, "
    "restart containers, deploy, roll back or change files.",
    "Reference validation checks that cited facts and observations exist, not "
    "that the statement follows from them.",
)


class AgentState(TypedDict, total=False):
    """Hold the run's counters, transcript and outcome between graph nodes."""

    steps: int
    tool_calls: int
    exchanges: list[ToolExchange]
    pending: ToolCall | None
    answer: FinalAnswer | None
    stop_reason: StopReason | None


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Provide per-run dependencies that are never part of the graph state."""

    provider: AgentProvider
    toolbox: ToolBox
    limits: AgentLimits
    clock: Callable[[], float]
    deadline: float


def _decide(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Ask the provider for one decision while steps and time remain."""
    context = runtime.context
    limits = context.limits
    steps = state["steps"]
    if steps >= limits.max_steps:
        return {"stop_reason": "step_limit"}
    if context.clock() >= context.deadline:
        return {"stop_reason": "time_limit"}
    turn = AgentTurn(
        step=steps + 1,
        remaining_steps=limits.max_steps - steps - 1,
        remaining_tool_calls=max(limits.max_tool_calls - state["tool_calls"], 0),
        tools=context.toolbox.specs,
        check_kinds=tuple(CHECKS),
        exchanges=tuple(state["exchanges"]),
    )
    try:
        raw = context.provider.decide(turn)
    except Exception:
        # Provider failures are external; their messages are not reported.
        return {"steps": steps + 1, "stop_reason": "provider_error"}
    if context.clock() > context.deadline:
        return {"steps": steps + 1, "stop_reason": "time_limit"}
    try:
        decision = parse_decision(raw, limits)
    except DecisionError as error:
        refused = ToolExchange(
            steps + 1, "invalid_decision", {}, {"status": "error", "error": str(error)}
        )
        return {"steps": steps + 1, "exchanges": [*state["exchanges"], refused]}
    if isinstance(decision, FinalAnswer):
        return {"steps": steps + 1, "answer": decision}
    return {"steps": steps + 1, "pending": decision}


def _after_decide(state: AgentState) -> str:
    """Route to a tool, to another decision or to validation."""
    if state.get("stop_reason") is not None or state.get("answer") is not None:
        return "finish"
    if state.get("pending") is not None:
        return "run_tool"
    return "decide"


def _run_tool(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Run the pending read-only tool, or refuse it once the call budget is spent."""
    context = runtime.context
    call = state["pending"]
    assert call is not None
    known = call.tool in {spec.name for spec in context.toolbox.specs}
    name = call.tool if known and TOOL_NAME.fullmatch(call.tool) else "unknown_tool"
    if state["tool_calls"] >= context.limits.max_tool_calls:
        result: dict[str, object] = {
            "status": "error",
            "error": "Tool call limit reached; finish with an answer.",
        }
        calls = state["tool_calls"]
    else:
        result = context.toolbox.run(call.tool, call.arguments)
        calls = state["tool_calls"] + 1
    exchange = ToolExchange(
        state["steps"], name, call.arguments if known else {}, result
    )
    return {
        "pending": None,
        "tool_calls": calls,
        "exchanges": [*state["exchanges"], exchange],
    }


def _finish(state: AgentState) -> AgentState:
    """End the graph; validation happens outside so the state stays small."""
    return {}


def build_agent_graph() -> CompiledStateGraph[
    AgentState, AgentContext, AgentState, AgentState
]:
    """Compile the decide → run_tool → decide loop with one exit to finish."""
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("decide", _decide)
    graph.add_node("run_tool", _run_tool)
    graph.add_node("finish", _finish)
    graph.add_edge(START, "decide")
    graph.add_conditional_edges(
        "decide", _after_decide, ["decide", "run_tool", "finish"]
    )
    graph.add_edge("run_tool", "decide")
    graph.add_edge("finish", END)
    return graph.compile(name="bounded_investigation")


def run_agent(
    view: InvestigationView,
    provider: AgentProvider,
    *,
    limits: AgentLimits = DEFAULT_AGENT_LIMITS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Run one bounded investigation and return its validated agent report."""
    toolbox = ToolBox(view, limits)
    context = AgentContext(
        provider, toolbox, limits, clock, clock() + limits.max_seconds
    )
    initial: AgentState = {
        "steps": 0,
        "tool_calls": 0,
        "exchanges": [],
        "pending": None,
        "answer": None,
        "stop_reason": None,
    }
    # Evidence must not leave the machine through LangSmith tracing.
    with tracing_context(enabled=False):
        final = build_agent_graph().invoke(
            initial,
            context=context,
            config={"recursion_limit": limits.max_steps * 2 + 4},
        )
    stop_reason = final.get("stop_reason")
    outcome = validate_answer(final.get("answer"), view, limits, stop_reason)
    return {
        "schema_version": AGENT_REPORT_VERSION,
        "subject": view.subject,
        "agent": {
            "graph": "bounded_investigation",
            "provider": provider.name,
            "status": "stopped" if stop_reason else "answered",
            "stop_reason": stop_reason,
            "steps": final["steps"],
            "tool_calls": final["tool_calls"],
            "limits": limits.as_json(),
        },
        **outcome,
        "tool_log": [
            {
                "step": exchange.step,
                "tool": exchange.tool,
                "arguments": exchange.arguments,
                "status": exchange.result["status"],
                **(
                    {"error": exchange.result["error"]}
                    if exchange.result["status"] == "error"
                    else {}
                ),
            }
            for exchange in final["exchanges"]
        ],
        "limitations": [*_LIMITATIONS, provider.limitation],
    }


def render_agent_report(report: dict[str, object]) -> str:
    """Render the agent report as stable ASCII-safe JSON."""
    return json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
