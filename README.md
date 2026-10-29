# AI DevOps Agent

A staged Python learning project for investigating incidents in a small Docker
application. The target workflow collects bounded logs, container state and
deployment history, builds an evidence timeline and proposes possible causes
with references to the observations behind them. An engineer reviews the report
and chooses the next action. Incident data used by the project is fictional.

The current implementation covers **Stages 01–06**: a FastAPI service with
`GET /health` and validated settings, plus a pure Python incident model,
reproducible evidence timeline and deterministic findings on fictional data.
An offline CLI accepts bounded fictional JSON fixtures, removes documented
sensitive text patterns and prints a report with evidence and data-quality gaps.
A disposable Docker lab produces a controlled failure, and a host collector
reads only an allowlisted container's selected state and bounded logs through
guarded `GET` requests into the same report.
Optional deployment tags from the fictional Git lab add evidence to the
timeline. Investigations can be saved in local SQLite with versioned inputs,
hashes and reports, then listed, shown and verified in another process.
A bounded LangGraph agent reads one saved investigation through seven read-only
tools and returns unconfirmed hypotheses that must cite existing facts, plus one
proposed diagnostic check. Its provider is deterministic and offline; a real
model adapter is a later step.
Facts, causal hypotheses and conflicting observations remain separate. Tests,
Ruff, strict mypy, locked dependencies and a GitHub Actions configuration are
included. Tests and the offline CLI need no model, Docker daemon, external database service or
credentials; the optional lab needs a local Docker Engine.
Prometheus, OpenTelemetry and a real model adapter are **planned**. The agent
has only bounded read-only tools; it cannot execute shell commands, restart
containers, deploy or apply fixes. Actual checks, publication state and limitations are recorded in
[verification](docs/VERIFICATION.md).

**Start with the [Stage 01 guide in Russian](docs/STAGE_01.md)** or the
[quickstart](docs/instructions/quickstart.md). After the server starts, the
interactive API documentation is at <http://127.0.0.1:8004/docs>.

## Roadmap

| Stage | Work | Status |
| --- | --- | --- |
| 01 | Python project, FastAPI, settings, health endpoint, tests and documentation | Implemented; checked locally, remote CI status in [verification](docs/VERIFICATION.md) |
| 02 | Incident, evidence and timeline domain model | Implemented; checked locally, remote CI status in [verification](docs/VERIFICATION.md) |
| 03 | Bounded input, normalization, redaction and CLI report | Implemented; checked locally, remote CI status in [verification](docs/VERIFICATION.md) |
| 04 | Docker lab and observation collector | Implemented; checked locally with a fake daemon, live lab and remote CI status in [verification](docs/VERIFICATION.md) |
| 05 | Investigation storage and deployment history | Implemented; local checks and remote publication in [verification](docs/VERIFICATION.md) |
| 06 | Bounded LangGraph investigation | Implemented with an offline provider; checks and publication in [verification](docs/VERIFICATION.md) |
| 07 | Prometheus and OpenTelemetry correlation | Planned |
| 08 | Investigation API, evaluations and container delivery | Planned |

Completion criteria for every stage are in the [roadmap](docs/ROADMAP.md).

## Documentation

Instructions ([docs/instructions/](docs/instructions/)):

- [Quickstart](docs/instructions/quickstart.md): requirements, local start, health check, optional settings
- [Using the API](docs/instructions/usage.md): health contract, routes, configuration and its precedence
- [Incident domain](docs/instructions/incident-domain.md): Python inputs, timeline, findings and boundaries
- [Offline investigation](docs/instructions/offline-investigation.md): fixture schema, CLI, limits, redaction and reports
- [Docker lab and collector](docs/instructions/docker-lab.md): lab scenario, collector options, allowlist, limits and gaps
- [Saved investigations and deployment history](docs/instructions/investigation-history.md): local lab, SQLite, idempotent saves, show and verify
- [Bounded investigation agent](docs/instructions/bounded-agent.md): graph, read-only tools, limits, validation and report
- [Testing and verification](docs/instructions/testing.md): lint, formatting, strict types, tests, documentation links, manual HTTP smoke check
- [CI and stage publication](docs/instructions/ci-and-delivery.md)
- [Project structure](docs/instructions/project-structure.md)

Learning guides in Russian:

- [Stage 01: FastAPI foundation](docs/STAGE_01.md)
- [Stage 02: Incident, evidence and timeline](docs/STAGE_02.md)
- [Stage 03: Bounded input and offline report](docs/STAGE_03.md)
- [Stage 04: Docker lab and bounded collector](docs/STAGE_04.md)
- [Stage 05: Saved investigations and deployment history](docs/STAGE_05.md)
- [Stage 06: Bounded LangGraph agent](docs/STAGE_06.md)
- [Roadmap and completion criteria](docs/ROADMAP.md)
- [CI/CD guide](docs/CI_CD.md)
- [Learning materials index](docs/guide/README.md) and
  [machine-readable verification](docs/guide/verification.json)
- [Project verification](docs/VERIFICATION.md)

Design and history:

- [Solution approach](docs/APPROACH.md)
- [Architecture decision 0001: staged read-only investigation](docs/adr/0001-staged-read-only-incident-investigation.md)
- [Architecture decision 0002: Docker access for the lab collector](docs/adr/0002-docker-lab-collector-access.md)
- [Architecture decision 0003: Immutable investigation records](docs/adr/0003-immutable-investigation-records.md)
- [Architecture decision 0004: Recorded lab deployments](docs/adr/0004-recorded-lab-deployments.md)
- [Architecture decision 0005: Bounded LangGraph agent](docs/adr/0005-bounded-langgraph-agent.md)
- [Development log](docs/DEVELOPMENT_LOG.md)
