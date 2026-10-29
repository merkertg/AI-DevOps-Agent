# ADR 0006: Allowlisted telemetry and trace-ID correlation

- Status: accepted
- Date: 2026-10-29

## Context

Stage 07 adds metrics and traces to the training lab. Prometheus accepts
arbitrary PromQL, and its HTTP API also offers administrative endpoints when
enabled. Trace backends expose every stored trace. Telemetry also arrives with
its own clocks and delays: a missing trace ID, a late or stopped metric and
skewed timestamps can make unrelated events look connected, and events at the
same time can look like cause and effect.

## Decision

- **Prometheus.** A host-side client sends only `GET /api/v1/query_range` to a
  loopback `http://host:port`. The expressions are fixed in code
  (`STAGE07_TELEMETRY`); a different expression is refused before any
  connection. The window is the incident interval; the step widens so a series
  never exceeds 500 points. Responses are limited to 1 MiB and 5 s. The lab
  starts Prometheus without the admin or lifecycle APIs and publishes it only on
  `127.0.0.1`.
- **Traces.** The lab's OpenTelemetry Collector writes spans to a JSON lines
  file. The investigator reads that file with size, line and span limits and
  keeps only spans whose resource names the allowlisted service. There is no
  query API for traces. Recorded Prometheus responses can replace the live
  client for offline and reproducible runs.
- **Evidence.** Each rise of a metric above its threshold becomes a
  `metric_signal`; each error span inside the window becomes a `trace_error`
  with its trace ID. Structured log lines keep a valid W3C `trace_id`.
  Observations from different sources sharing a trace ID produce a
  `same_trace` fact.
- **Gaps in code.** Refused connections without a trace ID, traces missing
  from the export, logs more than one second outside their spans, metrics with
  no sample in the last 60 s of the window, absent or ambiguous series and
  unavailable sources are written as gaps, so the report is `partial`.
- **Compatibility.** Trace IDs and the telemetry list appear in reports and
  stored inputs only when present; earlier reports and stored records keep
  their bytes and still verify.

## Consequences

The lab can show one incident consistently across logs, metrics and traces,
and the same code runs offline from recorded files. Telemetry shows symptoms;
correlation by trace ID or time establishes that records describe the same
request or moment, not why it failed. Simultaneous changes are not linked by
the deployment rule, which requires strictly later failures.

The one-second skew tolerance compares Docker receipt times with application
span times; larger or variable delays can hide or invent skew. Staleness is
measured at collection time, so a stopped process and a delayed scrape look
alike. Reading a file export assumes the developer points to the lab's own
output; it is not authenticated. Wider use would need daemon-side controls on
Prometheus and the trace backend.
