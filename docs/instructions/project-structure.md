# Project structure

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Where each part of the application, tests and documentation lives.

```text
.github/workflows/
  ci.yml                 # Lint, formatting, strict types, tests and documentation links
app/
  __init__.py            # Application version 0.1.0
  config.py              # Validated Settings and cached get_settings()
  main.py                # HealthResponse, create_app(), liveness handler and exported app
tests/
  conftest.py            # Clean APP_* environment, temporary working directory, fresh client
  test_config.py         # Defaults, validation, source precedence and settings cache
  test_health.py         # Health JSON, OpenAPI, methods, unknown routes, app instances
scripts/
  check_docs.py          # Local Markdown link targets; external URLs are not requested
docs/
  instructions/          # Quickstart, API usage, testing, CI and stage publication, this file
  guide/                 # Learning materials index and machine-readable verification
  adr/                   # Recorded architecture decisions
  STAGE_01.md            # FastAPI foundation learning guide in Russian
  ROADMAP.md             # Planned stages and completion criteria in Russian
  CI_CD.md               # CI and stage publication guide in Russian
  APPROACH.md            # Current design, boundaries and planned architecture
  VERIFICATION.md        # Checks actually run and their limits
  DEVELOPMENT_LOG.md     # Step-by-step implementation log
.env.example             # Non-secret configuration example
.gitignore               # Local environments, caches, .env and future private evidence
.python-version          # Python 3.12 selection
AGENTS.md                # Rules for future changes
pyproject.toml           # Metadata, dependency constraints and tool settings
uv.lock                  # Resolved dependency versions
README.md                # Short description, roadmap and documentation index
```

The request path is `client → Uvicorn → FastAPI → health → HealthResponse → JSON`.
Application instances receive their settings through `create_app()`.

## Planned responsibilities

Create new layers only when they own behavior:

| Future location | First responsibility |
| --- | --- |
| `app/domain/` | Incidents, evidence, timeline and deterministic findings |
| `app/application/` | Investigation use cases and ports |
| `app/infrastructure/` | Bounded input, Docker, storage, Git and model adapters |
| Lab configuration | A disposable Docker application and controlled incidents |
| Evaluation fixtures | Known observations and independent expected results |

These locations are design directions, not implemented directories. The original
stack remains planned: Docker for the lab, GitPython for history, LangGraph for
bounded orchestration, Prometheus and OpenTelemetry for correlation. See the
[roadmap](../ROADMAP.md) for their order and completion criteria.
