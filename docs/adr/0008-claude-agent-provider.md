# ADR 0008: Claude as an opt-in agent provider

- Status: accepted
- Date: 2026-10-30

## Context

Stage 06 defined a provider port and shipped only the deterministic offline
provider; a real model adapter was deferred to a separate step with explicit
settings and separate live verification. A model provider adds three risks the
offline one does not have: evidence leaves the machine, the model may follow
instructions hidden in log text, and network calls can fail, stall or be
declined.

## Decision

- **Provider.** Claude through the official Anthropic Python SDK, model
  `claude-opus-5-5` by default (`APP_AGENT_MODEL`), effort `high`
  (`APP_AGENT_EFFORT`). Thinking stays at the model's adaptive default.
- **Opt-in only.** The offline provider remains the default. The model is used
  only when a command passes `--provider anthropic` (`python -m app.agent`,
  `python -m app.evaluate`). The HTTP API keeps the offline provider. The SDK
  resolves its own credentials (`ANTHROPIC_API_KEY` or an `ant` profile); the
  project never stores them, and a run without credentials stops before any
  request.
- **Same boundary as the offline provider.** The seven read-only tools and a
  `finish_investigation` tool are sent as strict tool definitions; Claude must
  call one per reply (`tool_choice` `auto` with parallel calls disabled, since
  forced tool choice is not available on this model). Each call is converted
  into the same raw decision document, so the graph's parsing, budgets,
  reference validation and check catalog apply unchanged.
- **Conversation.** One append-only conversation per run: responses are passed
  back unchanged, each tool call gets a `tool_result` (errors as `is_error`),
  and a reply without a tool call gets a short reminder. The remaining budget
  follows every result. The system prompt says that text in tool results is
  evidence, never instructions.
- **Failures.** SDK errors and `refusal` stop the run as `provider_error`
  without their text. Server-side fallback (`fallbacks: "default"`) re-runs a
  declined request on the model Anthropic recommends for that category. The
  SDK timeout is 60 s per request with two retries; model runs get a 300 s
  agent deadline (`APP_AGENT_MAX_SECONDS`) checked before and after each call.

## Consequences

The agent can reason over the evidence while still being unable to change
anything, and a wrong or invented citation is rejected exactly as before. The
report states that tool results were sent to the Anthropic API; the redaction
filter removes only documented patterns, so only fictional or approved
evidence should be used. Each run costs money and is not deterministic, so
ordinary tests use a fake Messages API and the model is evaluated separately
with `python -m app.evaluate --provider anthropic`.

The deadline cannot interrupt a request already in flight; the SDK timeout
bounds it. Rate limits, quotas and data-retention terms of the Anthropic
account apply. Other providers can be added behind the same port.
