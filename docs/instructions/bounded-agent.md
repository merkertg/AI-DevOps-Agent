# Bounded investigation agent

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Stage 06 runs a LangGraph agent over one saved investigation. It reads that
investigation through read-only tools and returns unconfirmed hypotheses that
cite existing facts, plus one proposed diagnostic check. The
[Russian guide](../STAGE_06.md) explains each step; the boundary is recorded in
[ADR 0005](../adr/0005-bounded-langgraph-agent.md).

## Run

Save an investigation first, as in
[saved investigations](investigation-history.md), then pass its ID:

```bash
uv run --locked python -m app.agent "$id" --store var/stage05/investigations.sqlite3
```

The store is opened read-only. The saved record must pass `verify`; otherwise
the agent is not run. The provider is `offline-rules-v1`: fixed rules, no
language model, no network, no credentials. A real model adapter is a later step.

## Graph and limits

```text
START → decide ⇄ run_tool → … → decide → finish → END
```

`decide` asks the provider for one decision: call a listed tool or finish.
`run_tool` runs one read-only tool. Limits are enforced in code:

| Limit | Value | When exceeded |
| --- | --- | --- |
| Decisions | 12 | Stop with `step_limit` |
| Tool calls | 8 | Further calls refused; the provider is told to finish |
| Time | 30 s, checked before and after each decision | Stop with `time_limit` |
| Tool result | 8,000 characters | Error result; request a smaller page |
| Hypotheses / notes | 5 / 5 | Decision rejected |
| Model text | 600 characters, then redacted | Decision rejected |

A malformed decision consumes a step and is reported back to the provider.
A provider exception stops the run with `provider_error`; its message is not
reported. LangSmith tracing is disabled for the run.

## Tools

`get_overview`, `list_sources`, `get_timeline` (filters `source_id`, `kind`;
pages of up to 20 with messages shortened to 240 characters), `get_observation`,
`list_facts`, `list_rule_findings` and `get_data_quality`. All read the current
investigation only. Unknown tools and arguments return static errors. There is
no shell, exec, restart, deployment, rollback, file or repository tool.
Observation messages are returned as data; text that resembles an instruction
does not change what the offline provider does.

## Report

| Field | Meaning |
| --- | --- |
| `agent` | Provider, `answered` or `stopped`, stop reason, steps, tool calls, limits |
| `hypotheses` | Accepted, always `unconfirmed`, with `fact_ids` and `observation_ids` |
| `rejected_hypotheses` | Proposals citing no fact or unknown IDs, with reasons |
| `next_check` | A catalog check with an existing target; `status: proposed`, `executed: false` |
| `rejected_check` | A check outside the catalog or with an invalid target |
| `insufficient_data` | Missing knowledge stated by deterministic code |
| `model_notes` | Redacted provider notes |
| `tool_log` | Each request, its validated arguments and result status |

Check kinds: `review_dependency_endpoint` (refused connection),
`compare_deployed_revision` (deployment), `review_trace` (failed span, added in
Stage 07), `inspect_container_state` and
`widen_log_window` (sources of that kind), `collect_missing_source` (declared
source without observations). No check is executed by the project.

Exit `0` means a report was produced, including a stopped run. Exit `2` means
a missing store or ID, a record that does not verify, or invalid arguments.
