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
  domain/                # Pure Python incident models, evidence, timeline and rules
  application/
    inputs.py            # PreparedInput: sanitized evidence and data-quality metadata
    reporting.py         # build_report(), uncertainty and stable JSON rendering
  infrastructure/
    fixtures.py          # Bounded regular-file reader and strict JSON-to-domain adapter
    redaction.py         # Documented sensitive text patterns, replacement count, bounding
    docker_engine.py     # Guarded GET-only Docker Engine client over a Unix socket
    docker_lab.py        # Code allowlist, lab label check and Docker-to-domain mapping
  cli.py                 # Offline fixture investigation; stdout JSON, safe stderr
  collect.py             # Stage 04 lab collector CLI; stdout JSON, safe stderr
fixtures/stage03/         # Two fictional JSON examples: known signals and partial collection
lab/stage04/
  compose.yaml           # Disposable lab: one labeled service, no network, no Docker socket
  training_api.py        # Fictional process: structured refusal, then exit code 3
tests/
  conftest.py            # Clean APP_* environment, temporary working directory, fresh client
  test_config.py         # Defaults, validation, source precedence and settings cache
  test_health.py         # Health JSON, OpenAPI, methods, unknown routes, app instances
  test_domain*.py        # Domain validation, deterministic rules, evidence and contradictions
  test_fixtures.py       # Bounded intake, strict schema, safe errors and normalization
  test_redaction.py      # Supported filtering patterns and explicit unsupported cases
  test_reporting.py      # Evidence links, quality gaps, contradictions and deterministic JSON
  test_cli.py            # Real CLI, safe failures and independence from .env/site packages
  docker_fake.py         # Fake Docker Engine API on a Unix socket that records requests
  test_docker_engine.py  # Request guard, client surface, stream parsing, limits, errors
  test_docker_lab.py     # Allowlist, label, signals, gaps, redaction and the lab process
  test_collect_cli.py    # Collector CLI against the fake daemon and safe failures
scripts/
  check_docs.py          # Local Markdown link targets; external URLs are not requested
docs/
  instructions/          # Quickstart, API usage, testing, CI and stage publication, this file
  guide/                 # Learning materials index and machine-readable verification
  adr/                   # Recorded architecture decisions
  STAGE_01.md            # FastAPI foundation learning guide in Russian
  STAGE_02.md            # Incident domain and evidence learning guide in Russian
  STAGE_03.md            # Bounded intake, redaction and CLI learning guide in Russian
  STAGE_04.md            # Docker lab, access boundary and collector guide in Russian
  ROADMAP.md             # Planned stages and completion criteria in Russian
  CI_CD.md               # CI and stage publication guide in Russian
  APPROACH.md            # Current design, boundaries and planned architecture
  VERIFICATION.md        # Checks actually run and their limits
  DEVELOPMENT_LOG.md     # Step-by-step implementation log
.env.example             # Non-secret configuration example
.gitignore               # Local environments, caches, .env and private evidence under var/
.python-version          # Python 3.12 selection
AGENTS.md                # Rules for future changes
pyproject.toml           # Metadata, dependency constraints and tool settings
uv.lock                  # Resolved dependency versions
README.md                # Short description, roadmap and documentation index
```

The request path is `client → Uvicorn → FastAPI → health → HealthResponse → JSON`.
Application instances receive their settings through `create_app()`.

The separate Python domain path is `Incident + Source + Observation →
investigate() → Investigation`. The domain uses the standard library and does
not import the web app or read configuration. See [incident domain](incident-domain.md).

The offline path is `cli → load_fixture() → PreparedInput → render_report() →
investigate() → JSON`. The CLI composes the file adapter and application report;
the application imports domain types, while the domain remains independent of
adapters. See [offline investigation](offline-investigation.md).

The lab path is `collect → select_targets() → DockerEngineClient (GET inspect,
GET logs) → collect_lab() → PreparedInput → render_report() → JSON`. It reuses
the redaction and reporting of the offline path. See
[Docker lab and collector](docker-lab.md).

## Planned responsibilities

Create new layers only when they own behavior:

| Future location | First responsibility |
| --- | --- |
| Application ports | Storage or model providers when a use case needs them |
| Additional infrastructure adapters | Storage, Git and model integration |
| Further lab services | Telemetry and more controlled incidents (Stage 07) |
| Evaluation suite | Independent incident labels and quality thresholds |

These locations are design directions, not implemented directories. The original
stack continues as planned: GitPython for history, LangGraph for bounded
orchestration, Prometheus and OpenTelemetry for correlation. See the
[roadmap](../ROADMAP.md) for their order and completion criteria.
