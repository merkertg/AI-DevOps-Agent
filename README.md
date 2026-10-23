# AI DevOps Agent

A staged Python learning project for investigating incidents in a small Docker
application. The target workflow collects bounded logs, container state and
deployment history, builds an evidence timeline and proposes possible causes
with references to the observations behind them. An engineer reviews the report
and chooses the next action.

**Current implementation: Stage 01 — FastAPI foundation.** The service exposes
`GET /health`, validated settings and generated API documentation. It includes
isolated tests, Ruff, strict mypy, locked dependencies and a GitHub Actions
configuration. Actual checks, publication state and limitations are recorded in
[verification](docs/VERIFICATION.md).

Incident analysis, Docker collection, GitPython, LangGraph, Prometheus and
OpenTelemetry are **planned**. No model, Docker daemon, database or credentials
are required for Stage 01. The future agent will have bounded read-only tools;
it will not execute shell commands, restart containers, deploy or apply fixes.

## Quickstart

Use Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Run commands from this repository root:

```bash
uv sync --locked
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8004
```

In another terminal:

```bash
curl -i http://127.0.0.1:8004/health
```

Default response, HTTP `200`:

```json
{"status":"ok","service":"ai-devops-agent"}
```

Open [Swagger UI](http://127.0.0.1:8004/docs),
[ReDoc](http://127.0.0.1:8004/redoc) or
[OpenAPI JSON](http://127.0.0.1:8004/openapi.json). `/health` is a liveness check:
it confirms that this API can respond. It does not check Docker, a model or an
application under investigation. The root path `/` returns `404`.

Optional settings can come from the process environment or a local `.env` based
on [.env.example](.env.example). Defaults are sufficient to start:

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | `ai-devops-agent` | Nonempty service name; surrounding whitespace is removed |
| `APP_ENV` | `development` | One of `development`, `test`, `production` |
| `APP_DEBUG` | `false` | FastAPI debug mode; keep disabled for normal use |

Explicit `Settings(...)` values take priority over environment variables, then
`.env`, then defaults. Restart the process after changing configuration.
`APP_ENV=production` is a validated label; deployment configuration is future work.

## Verify

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app tests scripts
uv run --locked pytest
uv run --locked python scripts/check_docs.py
```

Tests run without external services or a developer's `.env`. A configured CI
workflow and passing local checks are separate from a confirmed GitHub Actions
run. Each completed stage is checked, documented, committed and pushed; remote
results are reported for the actual commit. See [CI](docs/CI_CD.md).

## Documentation

- [Detailed Stage 01 guide in Russian](docs/STAGE_01.md)
- [Roadmap and completion criteria in Russian](docs/ROADMAP.md)
- [Approach and boundaries](docs/APPROACH.md)
- [Quickstart](docs/instructions/quickstart.md), [API usage](docs/instructions/usage.md),
  [testing](docs/instructions/testing.md), [project structure](docs/instructions/project-structure.md)
- [CI and stage publication](docs/CI_CD.md)
- [Architecture decision](docs/adr/0001-staged-read-only-incident-investigation.md)
- [Development log](docs/DEVELOPMENT_LOG.md) and [verification](docs/VERIFICATION.md)
- [Learning guide index](docs/guide/README.md) and
  [machine-readable verification](docs/guide/verification.json)

The initial documentation layout follows the sibling AI Forge learning projects.
Layers and dependencies are added only when a stage needs them. The
[roadmap](docs/ROADMAP.md) preserves the intended stack while keeping later
capabilities clearly separate from shipped behavior.
