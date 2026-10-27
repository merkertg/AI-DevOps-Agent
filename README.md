# AI DevOps Agent

A staged Python learning project for investigating incidents in a small Docker
application. The target workflow collects bounded logs, container state and
deployment history, builds an evidence timeline and proposes possible causes
with references to the observations behind them. An engineer reviews the report
and chooses the next action. Incident data used by the project is fictional.

The current implementation covers **Stage 01**: a FastAPI service with
`GET /health`, validated settings and generated API documentation, isolated
tests, Ruff, strict mypy, locked dependencies and a GitHub Actions
configuration. No model, Docker daemon, database or credentials are required.
Incident analysis, Docker collection, GitPython, LangGraph, Prometheus and
OpenTelemetry are **planned**. The future agent will have bounded read-only
tools; it will not execute shell commands, restart containers, deploy or apply
fixes. Actual checks, publication state and limitations are recorded in
[verification](docs/VERIFICATION.md).

**Start with the [Stage 01 guide in Russian](docs/STAGE_01.md)** or the
[quickstart](docs/instructions/quickstart.md). After the server starts, the
interactive API documentation is at <http://127.0.0.1:8004/docs>.

## Roadmap

| Stage | Work | Status |
| --- | --- | --- |
| 01 | Python project, FastAPI, settings, health endpoint, tests and documentation | Implemented; checked locally, remote CI status in [verification](docs/VERIFICATION.md) |
| 02 | Incident, evidence and timeline domain model | Planned |
| 03 | Bounded input, normalization, redaction and CLI report | Planned |
| 04 | Docker lab and observation collector | Planned |
| 05 | Investigation storage and deployment history | Planned |
| 06 | Bounded LangGraph investigation | Planned |
| 07 | Prometheus and OpenTelemetry correlation | Planned |
| 08 | Investigation API, evaluations and container delivery | Planned |

Completion criteria for every stage are in the [roadmap](docs/ROADMAP.md).

## Documentation

Instructions ([docs/instructions/](docs/instructions/)):

- [Quickstart](docs/instructions/quickstart.md): requirements, local start, health check, optional settings
- [Using the API](docs/instructions/usage.md): health contract, routes, configuration and its precedence
- [Testing and verification](docs/instructions/testing.md): lint, formatting, strict types, tests, documentation links, manual HTTP smoke check
- [CI and stage publication](docs/instructions/ci-and-delivery.md)
- [Project structure](docs/instructions/project-structure.md)

Learning guides in Russian:

- [Stage 01: FastAPI foundation](docs/STAGE_01.md)
- [Roadmap and completion criteria](docs/ROADMAP.md)
- [CI/CD guide](docs/CI_CD.md)
- [Learning materials index](docs/guide/README.md) and
  [machine-readable verification](docs/guide/verification.json)
- [Project verification](docs/VERIFICATION.md)

Design and history:

- [Solution approach](docs/APPROACH.md)
- [Architecture decision 0001: staged read-only investigation](docs/adr/0001-staged-read-only-incident-investigation.md)
- [Development log](docs/DEVELOPMENT_LOG.md)
