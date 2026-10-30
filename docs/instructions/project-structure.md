# Project structure

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Where each part of the application, tests and documentation lives.

```text
.github/workflows/
  ci.yml                 # checks (lint, types, tests, docs, evaluations) and container jobs
app/
  __init__.py            # Application version 0.8.0
  config.py              # Validated Settings and cached get_settings()
  main.py                # HealthResponse, create_app(), liveness and the investigation router
  api.py                 # Token-scoped /investigations routes over inline fixtures
  evaluate.py            # Labelled-incident metrics and the corruption self-check
  domain/                # Pure Python incident models, evidence, timeline and rules
  application/
    inputs.py            # PreparedInput: sanitized evidence and data-quality metadata
    reporting.py         # build_report(), uncertainty and stable JSON rendering
    snapshots.py         # Canonical prepared input encoding and strict decoding
    records.py           # Store protocol, immutable records, hashes and reproduction checks
  infrastructure/
    fixtures.py          # Bounded regular-file reader and strict JSON-to-domain adapter
    redaction.py         # Documented sensitive text patterns, replacement count, bounding
    docker_engine.py     # Guarded GET-only Docker Engine client over a Unix socket
    docker_lab.py        # Code allowlist, lab label check and Docker-to-domain mapping
    deployments.py       # Allowlisted deployment metadata, time checks and evidence mapping
    git_history.py       # Read annotated Git objects through GitPython without writes
    sqlite_store.py      # Atomic append-only local store and read-only inspection
    telemetry.py         # Allowlisted metric queries, limits and metric/span mapping
    prometheus.py        # GET-only loopback query_range client and recorded responses
    otlp_traces.py       # Bounded OTLP JSON lines trace export reader
  agent/
    contracts.py         # Limits, provider port and strict parsing of untrusted decisions
    tools.py             # Frozen investigation view and seven read-only, paged tools
    validation.py        # Fact-reference checks, diagnostic check catalog, missing data
    graph.py             # LangGraph decide → run_tool → finish loop and agent report
    offline.py           # Deterministic offline provider on fixed rules
    __main__.py          # python -m app.agent ID --store PATH
  cli.py                 # Offline fixture investigation; stdout JSON, safe stderr
  collect.py             # Stage 04 lab collector CLI; stdout JSON, safe stderr
  cli_support.py         # Shared optional repository/store composition and safe arguments
  investigations.py      # Read-only list, show and verify of saved investigations
fixtures/stage03/         # Two fictional JSON examples: known signals and partial collection
fixtures/stage05/         # Fictional failure after a recorded lab deployment
fixtures/stage07/         # Fictional incident, recorded metrics and trace export
evaluations/
  cases/                 # Six hand-written labels of known fictional incidents
  fixtures/              # Extra fictional inputs used only by the evaluations
lab/stage04/
  compose.yaml           # Disposable lab: one labeled service, no network, no Docker socket
  training_api.py        # Fictional process: structured refusal, then exit code 3
lab/stage05/
  make_history.py        # Developer setup: create fictional repository or add lab tag
lab/stage07/
  compose.yaml           # Service, OpenTelemetry Collector and loopback-only Prometheus
  telemetry_api.py       # Fictional process: logs with trace IDs, a counter and spans
  prometheus.yml         # Scrape configuration for the lab service
  otel-collector.yaml    # OTLP/HTTP receiver writing JSON lines to var/stage07/
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
  git_lab.py             # Isolated fictional Git repository helper
  test_snapshots.py      # Canonical encoding, strict decoding and rejected snapshots
  test_records.py        # Hashes, identity, metadata and report reproduction
  test_sqlite_store.py   # Persistent records, atomic deduplication and append controls
  test_git_history.py    # Read-only allowlisted Git history and bounded objects
  test_deployments.py    # Structured tags, explicit deploy times, gaps and filtering
  test_domain_deployments.py # Deployment facts and cautious temporal hypotheses
  test_investigations_cli.py # Real save/read/verify processes, old reports and safe errors
  agent_support.py       # Fictional agent inputs and scripted providers
  test_agent_contracts.py # Strict decision parsing and limit validation
  test_agent_tools.py    # Read-only catalog, paging, arguments and result size
  test_agent_validation.py # Fact references, check catalog, redaction and missing data
  test_agent_graph.py    # Limits, provider errors, injections, determinism, no network
  test_agent_cli.py      # Saved-investigation runs, refusals and an unchanged store
  telemetry_fakes.py     # Fake loopback Prometheus and in-memory telemetry readers
  test_telemetry_evidence.py # Domain, metric/span mapping and correlation gaps
  test_telemetry_readers.py  # Prometheus client, recorded metrics, OTLP reader
  test_telemetry_cli.py  # CLIs with recorded, fake-live and missing telemetry
  test_telemetry_lab.py  # Lab process without Docker and compose consistency
  test_api.py            # Token scopes, idempotent saves, reads, errors and OpenAPI
  test_api_smoke.py      # Real Uvicorn process with the smoke script
  test_evaluate.py       # Labelled cases, every corruption detected, case parsing
scripts/
  check_docs.py          # Local Markdown link targets; external URLs are not requested
  api_smoke.py           # End-to-end API check against a running server or container
docs/
  instructions/          # Quickstart, API usage, testing, CI and stage publication, this file
  guide/                 # Learning materials index and machine-readable verification
  adr/                   # Recorded architecture decisions
  STAGE_01.md            # FastAPI foundation learning guide in Russian
  STAGE_02.md            # Incident domain and evidence learning guide in Russian
  STAGE_03.md            # Bounded intake, redaction and CLI learning guide in Russian
  STAGE_04.md            # Docker lab, access boundary and collector guide in Russian
  STAGE_05.md            # SQLite records, verification and deployment history in Russian
  STAGE_06.md            # Bounded LangGraph agent, tools, limits and validation in Russian
  STAGE_07.md            # Metrics, traces, correlation gaps and the telemetry lab in Russian
  STAGE_08.md            # HTTP API, evaluations and container delivery in Russian
  ROADMAP.md             # Planned stages and completion criteria in Russian
  CI_CD.md               # CI and stage publication guide in Russian
  APPROACH.md            # Current design, boundaries and planned architecture
  VERIFICATION.md        # Checks actually run and their limits
  DEVELOPMENT_LOG.md     # Step-by-step implementation log
.env.example             # Non-secret configuration example
Dockerfile               # Two-stage non-root API image pinned by digest
.dockerignore            # Only pyproject.toml, uv.lock, README.md and app/ enter the image
.gitignore               # Local environments, caches, .env and private evidence under var/
.python-version          # Python 3.12 selection
AGENTS.md                # Rules for future changes
pyproject.toml           # Metadata, dependency constraints and tool settings
uv.lock                  # Resolved dependency versions
README.md                # Short description, roadmap and documentation index
```

The request path is `client → Uvicorn → FastAPI → health → HealthResponse → JSON`.
Application instances receive their settings through `create_app()`.
Investigation routes add `bearer token → require_read()/require_write() →
parse_fixture() → build_record() → SqliteInvestigationStore`; see
[using the API](usage.md).

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

Stage 05 extends either input path with optional `--repository` evidence and
`--store` persistence. `build_record()` encodes the prepared input and original
report; `SqliteInvestigationStore` inserts once. `investigations list/show/verify`
reads the stored snapshot in a later process. See
[saved investigations and deployment history](investigation-history.md).

The agent path is `python -m app.agent → find_record() → verify_record() →
build_view() → run_agent() (LangGraph: decide ⇄ run_tool → finish) →
validate_answer() → JSON`. The graph composes application tools; the domain and
application layers do not import LangGraph. See
[bounded investigation agent](bounded-agent.md).

Stage 07 extends either input path with `with_telemetry()`: `PrometheusClient`
or `RecordedPrometheus` and `OtlpFileReader` → `collect_telemetry()` →
`add_telemetry_evidence()` (`same_trace` facts and correlation gaps) → the same
report, store and agent. See [telemetry](telemetry.md).

## Planned responsibilities

Create new layers only when they own behavior:

| Future location | First responsibility |
| --- | --- |
| Model adapter behind `AgentProvider` | Real language model with explicit settings |
| More evaluation cases | Further labelled incidents, including from the live labs |

These locations are design directions, not implemented directories. See the
[roadmap](../ROADMAP.md) for their order and completion criteria.
