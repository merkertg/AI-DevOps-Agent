# ADR 0005: Bounded LangGraph agent with an offline provider

- Status: accepted
- Date: 2026-10-29

## Context

Stage 06 adds an investigation agent. A model-driven loop can call tools many
times, follow instructions hidden in log text, invent references or propose
actions that change systems. Stages 02–05 already produce validated evidence,
deterministic facts and saved, verifiable investigations. The agent should add
structured, checkable suggestions on top of them, not a new path to
infrastructure.

The user chose to deliver the offline provider first and connect a real model
adapter in a later, separate step.

## Decision

Build a LangGraph `StateGraph` with three nodes: `decide`, `run_tool` and
`finish`. The graph runs over one saved investigation that passes `verify`;
the CLI opens the store read-only and never writes it.

- **Provider port.** A provider receives a turn with the tool catalog, the
  diagnostic check catalog, remaining budgets and earlier tool results. It
  returns one raw document. The document is untrusted: strict parsing accepts
  only `call_tool` with a listed tool name and small JSON arguments, or
  `finish` with bounded hypotheses, an optional check and notes. Malformed
  output consumes a step and is reported back; exceptions stop the run as
  `provider_error` without their messages.
- **Tools.** Seven read-only functions over a frozen view of the current
  investigation: overview, sources, paged timeline, one observation, paged
  facts, paged rule findings and data quality. There is no shell, exec,
  restart, deployment, rollback, file or repository tool, and no Docker
  client or Git reader is reachable from the graph.
- **Limits in code.** 12 decisions, 8 tool calls, 30 seconds checked before
  and after every decision, 8,000 characters per tool result, 5 hypotheses,
  600 characters per model text and 5 notes. A LangGraph recursion limit is a
  backstop. Calls over the budget are refused and recorded.
- **Validation.** Each hypothesis must cite at least one existing fact ID and
  only existing observation IDs; otherwise it is rejected with reasons.
  Accepted hypotheses are always `unconfirmed`. The next check must be a kind
  from a fixed diagnostic catalog with an existing target of the matching
  kind; it is reported as `proposed` and never executed. Model text is
  redacted and bounded. Deterministic code lists missing knowledge.
- **No egress.** LangSmith tracing is disabled for the run, so tracing
  variables in the environment cannot send evidence elsewhere.
- **Offline provider.** `offline-rules-v1` reads five tools in a fixed order
  and answers from structured fact codes and IDs. It never interprets
  observation messages.

## Consequences

The agent cannot change the investigated systems: its only capabilities are
reads of one already-collected investigation. A future model adapter can be
added behind the same port and inherits the same parsing, limits and
validation. It needs its own explicit settings, request timeout and separate
live verification, and must not become a condition of ordinary tests.

Reference validation proves that cited facts and observations exist, not that
a statement follows from them. The time limit cannot interrupt a provider
call that never returns; a network provider must enforce its own timeout.
The offline provider demonstrates the protocol and limits; it is not a
language model and does not reason beyond fixed rules.
