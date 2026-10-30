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
the agent is not run. The default provider is `offline-rules-v1`: fixed rules,
no language model, no network, no credentials. `--provider anthropic` uses
Claude instead; see [Claude provider](#claude-provider).

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

## Claude provider

Stage 09 adds Claude behind the same port
([ADR 0008](../adr/0008-claude-agent-provider.md),
[Russian guide](../STAGE_09.md)):

```bash
uv run --locked python -m app.agent "$id" --store var/stage05/investigations.sqlite3 --provider anthropic
```

The official Anthropic SDK resolves credentials from `ANTHROPIC_API_KEY` or an
`ant auth login` profile; keep them out of the project. Without credentials the
command exits `2` before any request. **Tool results, including sanitized
evidence text, are sent to the Anthropic API**; the report says so in
`limitations`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `APP_AGENT_MODEL` | `claude-opus-5-5` | Claude model ID |
| `APP_AGENT_EFFORT` | `high` | `low`, `medium`, `high`, `xhigh` or `max` |
| `APP_AGENT_MAX_SECONDS` | `300` | Agent deadline for model runs, `10..1800` |
| `APP_AGENT_REQUEST_TIMEOUT_SECONDS` | `60` | SDK timeout per request (two retries) |

The seven read-only tools and `finish_investigation` are strict tool
definitions; Claude calls exactly one per reply (`tool_choice` `auto`, parallel
calls disabled). Each call becomes the same decision document as the offline
provider's, so parsing, budgets, reference validation and the check catalog are
unchanged. The conversation is append-only: responses, including thinking
blocks, are passed back as received; refused calls and invalid answers return
as `is_error` results; a reply without a tool call gets a reminder. The system
prompt states that text in tool results is evidence, never instructions.
Refusals and API errors stop the run as `provider_error` without their text;
server-side fallback (`fallbacks: "default"`) retries a declined request on the
recommended model. Runs are not deterministic and cost money. The HTTP API keeps
the offline provider. Evaluate the model separately with
`uv run --locked python -m app.evaluate --provider anthropic`.
