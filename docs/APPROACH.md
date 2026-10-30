# AI DevOps Agent — Solution Approach

[← README](../README.md) · [Roadmap](ROADMAP.md) · [Architecture decision](adr/0001-staged-read-only-incident-investigation.md)

## Problem and product goal

An engineer investigates one fictional incident affecting a small Docker
application. The eventual system will combine logs, container observations,
deployment metadata and telemetry for a stated time window. Its report will
separate observed facts, deterministic findings, possible causes, missing
evidence and suggested checks. A recent deployment is context, not proof that it
caused an error.

The first useful investigation will use local fictional fixtures. A complete
offline path comes before live Docker access or a model provider. This gives
each new adapter a stable contract and makes regressions reproducible.

## Current implementation: Stages 01–09

Stage 01 contains a small FastAPI application:

```text
client → Uvicorn → FastAPI → health handler → HealthResponse → JSON
                         ↑
                  create_app(Settings)
```

`app/config.py` validates settings and caches their normal process-level load.
`create_app()` accepts explicit settings so tests can construct independent
applications. `app/main.py` owns the application factory, response contract and
health handler. The response contains only `status` and `service`.

`GET /health` is liveness only. It does not claim that a Docker host, investigated
application, telemetry source or model is healthy. These integrations do not
exist at this stage.

Stage 02 adds a separate standard-library domain:

```text
Incident + Source + Observation
               ↓
         investigate()
               ↓
Investigation: timeline + facts + hypotheses + contradictions
```

Frozen dataclasses validate identifiers, aware timestamps, source types and exit
codes. Incident scope is one service and an inclusive event-time interval.
Timestamps normalize to UTC; collection may follow the incident window but must
not precede its event. The domain rejects duplicate identifiers, unknown sources,
out-of-scope observations and unresolved evidence references. Canonical ordering
makes the result independent of input order.

Rules use structured observation kinds rather than log prose. They record an
explicit connection refusal or nonzero container exit as a fact and keep causal
explanations as unconfirmed hypotheses. Conflicting exit codes for the same
source and event instant are retained and reported; their causal hypothesis is
suppressed. An empty timeline is valid and establishes no cause. File parsing,
normalization and redaction belong to Stage 03. The
[Stage 02 guide](STAGE_02.md) describes the current contracts and limits.

Stage 03 adds an offline workflow:

```text
regular UTF-8 JSON file → bounded fixture adapter → PreparedInput
                                                     ↓
CLI stdout ← stable JSON report ← build_report() ← investigate()
```

The fixture adapter rejects oversized input, unsupported schema fields, invalid
types and inconsistent domain evidence. It normalizes metadata and timestamps,
removes documented sensitive text patterns and then truncates long messages
with explicit metadata. Reports expose gaps, upstream truncation, missing-source
coverage and unknown circumstances. A complete flag is a producer declaration,
not proof of complete collection. The CLI never echoes raw file/argument values
in error diagnostics. See [offline investigation](instructions/offline-investigation.md).

Stage 04 adds a disposable Docker lab and a host collector:

```text
lab container (label stage04) → Docker daemon → Unix socket
                                                     ↓
collect CLI → allowlist → guarded GET inspect/logs → selected fields + bounded logs
                                                     ↓
                    PreparedInput(origin="docker_lab") → same report
```

The allowlist is code. One guard permits only `GET` on the inspect and logs
paths of allowlisted names with fixed query parameters. The collector requires
the exact name and lab label, copies selected state fields and discards
environment, mounts and command. Log lines become signals only through an exact
structured `event` field; exits come only from Docker state. Missing containers,
truncation and malformed or out-of-window lines become gaps. The guard protects
against the collector's own mistakes; it does not reduce socket privileges, as
recorded in [ADR 0002](adr/0002-docker-lab-collector-access.md). See
[Docker lab and collector](instructions/docker-lab.md).

Stage 05 adds optional deployment history and persisted investigations:

```text
PreparedInput + allowlisted annotated Git tags → domain report
                                                   ↓
canonical input + original report + versions + SHA-256 → SQLite
                                                   ↓
                          later process: list / show / verify
```

GitPython reads the fictional repository identified by its pinned root object.
Only explicit deployment metadata in annotated `deploy/lab/` tags becomes a
deployment observation; commit time is not deployment time. The domain may
connect a preceding deployment to a later failure signal, with both references
and a causal caveat. Missing or skipped history remains a gap. The local reader
does not modify the working tree, index, refs or remote. Identity conventions
and finite object limits do not make it a hostile-repository sandbox.

SQLite stores canonical prepared evidence, quality metadata, the original
report, versions and hashes. Atomic insertion deduplicates by full input hash;
repeat saves return the original record. Known-schema triggers reject normal
replacement/update/delete. Read-only inspection preserves the stored report;
verification compares hashes and regenerates it with current code. Hashes and
triggers are consistency controls, not authentication against a file owner.
See [ADR 0003](adr/0003-immutable-investigation-records.md),
[ADR 0004](adr/0004-recorded-lab-deployments.md) and
[saved investigations](instructions/investigation-history.md).

Stage 06 adds a bounded agent over one saved investigation:

```text
saved record → verify → frozen view (facts, observations, rule findings, quality)
                                  ↓
LangGraph: decide ⇄ run_tool (7 read-only tools) → finish
                                  ↓
validated answer: cited unconfirmed hypotheses + catalog check + missing data
```

The provider returns untrusted documents that are parsed strictly. Steps, tool
calls, time and result sizes are limited in code. Hypotheses must cite existing
fact IDs; the next check comes from a fixed diagnostic catalog and is never
executed. LangSmith tracing is disabled for the run. The only provider is the
deterministic `offline-rules-v1`; a real model adapter is a later step. See
[ADR 0005](adr/0005-bounded-langgraph-agent.md) and the
[bounded agent](instructions/bounded-agent.md).

Stage 07 adds telemetry to either input path:

```text
loopback Prometheus (2 allowlisted range queries) ─┐
OTLP JSON lines export (bounded, one service) ─────┼→ metric_signal / trace_error
structured log lines with trace_id ────────────────┘            ↓
             same_trace facts + gaps: no trace ID, missing trace, clock skew,
             stale or unavailable metric → same report, store and agent
```

The disposable lab runs the service, an OpenTelemetry Collector and Prometheus
on an internal network; only Prometheus is published on loopback. Recorded
responses and exports make the same code reproducible offline. Correlation by
trace ID or time is not stated as a cause. See
[ADR 0006](adr/0006-allowlisted-telemetry.md) and
[telemetry](instructions/telemetry.md).

Stage 08 completes the user path:

```text
client + bearer token → /investigations (inline fixture only)
      → same intake, store, verification and offline agent as the CLIs
python -m app.evaluate → six labelled incidents + ten corruptions each
Dockerfile → non-root image → CI build + API smoke test
```

The investigation routes stay closed until SHA-256 digests of a read and a
write token are configured. The API accepts no paths or URLs, so it cannot make
the server read Docker, Git or telemetry. Repeated saves of the same input
return the original record. Evaluations compare the product's JSON with
hand-written labels and fail when any deliberate corruption passes. The image is
built and smoke-tested in CI; it is not pushed or deployed. See
[ADR 0007](adr/0007-token-scoped-api-and-container.md),
[using the API](instructions/usage.md), [evaluations](instructions/evaluations.md)
and [container delivery](instructions/container.md).

Stage 09 connects Claude to the Stage 06 provider port. `--provider anthropic`
sends strict definitions of the same read-only tools to `claude-opus-5-5`
through the official SDK and turns each tool call into the same decision
document, so budgets, parsing, reference validation and the check catalog are
unchanged. The conversation is append-only; refusals and API errors stop the
run without their text; a run without credentials does not start. Tool results
leave the machine, which the report states. See
[ADR 0008](adr/0008-claude-agent-provider.md) and the
[bounded agent](instructions/bounded-agent.md#claude-provider).

## Stack and responsibility

| Component | Responsibility | Status |
| --- | --- | --- |
| Python 3.12, uv, `uv.lock` | Runtime and reproducible environment | Implemented |
| Standard-library dataclasses, enums and datetime | Immutable incident scope, timeline and evidence-linked rules | Implemented, Stage 02 |
| Standard-library JSON, file adapter and argparse | Bounded offline intake, supported redaction and CLI report | Implemented, Stage 03 |
| FastAPI, Uvicorn | Health and the token-scoped investigation API | Implemented, Stages 01 and 08 |
| Docker image (python slim, uv) | Non-root API container built and smoke-tested in CI | Implemented, Stage 08 |
| Evaluation suite (standard library) | Labelled incidents, quality metrics and corruption detection | Implemented, Stage 08 |
| Pydantic, pydantic-settings | Response contract and settings validation | Implemented |
| pytest, Ruff, mypy | Behavior, style and strict type checks | Implemented |
| GitHub Actions | Continuous integration | Configured; remote result in [verification](VERIFICATION.md) |
| Docker Engine API over a Unix socket (standard library) | Disposable lab and guarded read-only collector | Implemented, Stage 04 |
| GitPython | Read-only deployment history of the fictional allowlisted lab | Implemented, Stage 05 |
| SQLite (standard library) | Append-only local records, canonical input and reproduction checks | Implemented, Stage 05 |
| LangGraph | Bounded investigation orchestration with an offline provider | Implemented, Stage 06 |
| Anthropic Python SDK (`anthropic`) | Opt-in Claude provider behind the agent port | Implemented, Stage 09 |
| Prometheus HTTP API (standard library), OTLP JSON export | Allowlisted metric queries, bounded trace reading and trace-ID correlation | Implemented, Stage 07 |

Runtime dependencies for later stages are introduced with the corresponding
implementation, rather than installed in anticipation.

## Architecture

Grow a modular monolith as behavior appears:

| Layer | Responsibility | Added from |
| --- | --- | --- |
| Domain | Incident scope, evidence references, timeline and deterministic findings | Stage 02 |
| Application | Investigation use cases, input/output ports and report generation | Stage 03 |
| Infrastructure | Bounded file adapters, Docker collection, persistence, Git and telemetry readers; model adapters later | Stages 03–07 |
| Agent orchestration | Bounded LangGraph investigation over application tools | Stage 06 |
| API | Token-scoped routes over the same intake, store, verification and agent | Stage 08 |

Domain code depends on ordinary Python types rather than FastAPI, Docker,
GitPython, LangGraph or storage frameworks. Adapters translate external data into
that domain. Future directories are not created as empty placeholders; see
[project structure](instructions/project-structure.md#planned-responsibilities).

## Evidence and model boundaries

Each observation retains its source identifier, event time and received time.
Every fact, hypothesis and contradiction links back to existing observations.
The domain validates reference resolution, not semantic truth: a known reference
alone does not establish that an arbitrary external claim follows from it.
Stage 03 adds bounded input and explicit gaps/truncation reporting. It does not
independently establish collector completeness or detect all stale evidence.

Before live collection, Stage 03 limits local input size, record counts, incident
duration and retained text. Redaction supports documented key assignments,
Bearer credentials, URL userinfo, recognizable token shapes and ASCII emails.
It has tests and explicit exclusions, including arbitrary names, IP addresses,
encoded or unknown secrets. This filter does not guarantee arbitrary logs are
safe for persistence or model calls. The examples remain fictional.

The agent receives only allowlisted read-only tools with validated arguments
over one saved investigation. It may propose an evidence-linked hypothesis or
a diagnostic check from a fixed catalog. Deterministic code enforces scope, tool
budgets and fact references. Log text is untrusted data, including text that
looks like an instruction to the agent. Missing or conflicting evidence remains
visible in the agent report.

There is no agent tool for shell execution, container exec, restart,
deployment, file modification or automatic repair. Stage 04 reads Docker through
a host collector in a disposable lab, not through a model tool. A read-only
application method alone is not a permission boundary for an unrestricted Docker
socket: the [Docker Engine API](https://docs.docker.com/reference/api/engine/)
exposes every daemon operation to the socket holder. The collector's guard limits
its own requests; the process still holds the developer's Docker rights. Shared
or remote use needs daemon-side enforcement, per
[ADR 0002](adr/0002-docker-lab-collector-access.md). The agent receives
collected observations, not the Docker client or the Git reader.

## Verification and isolation

Tests use in-process clients, a temporary working directory and a cleaned
`APP_*` environment, so a developer's `.env`, network services and credentials
cannot change their result. Python 3.12, uv and `uv.lock` make the foundation
reproducible. `--locked` makes uv fail when project metadata requires a lock
change instead of updating it silently; see
[uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).
Commands are listed in [testing](instructions/testing.md).

## CI and stage delivery

Every stage needs a working result, meaningful checks, a Russian walkthrough,
updated usage instructions and factual verification. The stage is then committed
and pushed, as requested for this project. Check the remote ref and the CI result
for that commit separately. A failed push or pending CI is recorded explicitly.
The procedure is in [CI and stage publication](instructions/ci-and-delivery.md)
and the detailed [CI/CD guide](CI_CD.md).

## Next steps

All planned stages are implemented. Natural next steps are a live evaluation
of the Claude provider, user identities and token rotation for the API, more
labelled incidents, and registry publication with signing and scanning.
Each needs its own decision and checks; see the [roadmap](ROADMAP.md).
