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

## Current implementation: Stages 01–04

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

## Stack and responsibility

| Component | Responsibility | Status |
| --- | --- | --- |
| Python 3.12, uv, `uv.lock` | Runtime and reproducible environment | Implemented |
| Standard-library dataclasses, enums and datetime | Immutable incident scope, timeline and evidence-linked rules | Implemented, Stage 02 |
| Standard-library JSON, file adapter and argparse | Bounded offline intake, supported redaction and CLI report | Implemented, Stage 03 |
| FastAPI, Uvicorn | HTTP API and ASGI server | Implemented |
| Pydantic, pydantic-settings | Response contract and settings validation | Implemented |
| pytest, Ruff, mypy | Behavior, style and strict type checks | Implemented |
| GitHub Actions | Continuous integration | Configured; remote result in [verification](VERIFICATION.md) |
| Docker Engine API over a Unix socket (standard library) | Disposable lab and guarded read-only collector | Implemented, Stage 04 |
| GitPython | Read-only deployment history | Planned, Stage 05 |
| LangGraph | Bounded investigation orchestration | Planned, Stage 06 |
| Prometheus, OpenTelemetry | Telemetry correlation | Planned, Stage 07 |

Runtime dependencies for later stages are introduced with the corresponding
implementation, rather than installed in anticipation.

## Architecture

Grow a modular monolith as behavior appears:

| Layer | Responsibility | Added from |
| --- | --- | --- |
| Domain | Incident scope, evidence references, timeline and deterministic findings | Stage 02 |
| Application | Investigation use cases, input/output ports and report generation | Stage 03 |
| Infrastructure | Bounded file adapters, Docker collection, persistence, Git and model adapters | Stages 03–06 |
| Agent orchestration | Bounded LangGraph investigation over application tools | Stage 06 |
| API | Validated requests and reports exposed through application use cases | Stage 08 |

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

The model will receive only allowlisted read-only tools with validated arguments.
It may propose an evidence-linked hypothesis or a further diagnostic check.
Deterministic code will enforce incident scope, tool budgets and source
references. Log text is untrusted data, including text that looks like an
instruction to the agent. Missing or conflicting evidence must remain visible.

There will be no model tool for shell execution, container exec, restart,
deployment, file modification or automatic repair. Stage 04 reads Docker through
a host collector in a disposable lab, not through a model tool. A read-only
application method alone is not a permission boundary for an unrestricted Docker
socket: the [Docker Engine API](https://docs.docker.com/reference/api/engine/)
exposes every daemon operation to the socket holder. The collector's guard limits
its own requests; the process still holds the developer's Docker rights. Shared
or remote use needs daemon-side enforcement, per
[ADR 0002](adr/0002-docker-lab-collector-access.md). The future agent will
receive collected observations, not the Docker client.

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

Stage 05 stores investigations with their normalized inputs and reads the
deployment history of an allowed repository through GitPython without changing
it. The [roadmap](ROADMAP.md) lists the following stages and their completion
criteria.
