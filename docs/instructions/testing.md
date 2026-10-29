# Testing and verification

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Lint, formatting, strict type checks, tests, documentation links and a manual
HTTP smoke check.

## Verify

Run from the repository root:

```bash
uv sync --locked
uv lock --check --offline
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app tests scripts
uv run --locked pytest
uv run --locked python scripts/check_docs.py
git diff --check
```

Every command must exit `0`. [The verification report](../VERIFICATION.md)
records the actual date, results, test count and any limits; this instruction
does not assert a test run.

## What the tests cover

Tests construct the FastAPI app in process, isolate its configuration from a
developer's `.env`, and require no network service, credentials or Docker daemon.
The important contracts include health JSON, OpenAPI, rejected methods, unknown
routes, configuration validation and precedence, cached loading and independent
application instances. The
[FastAPI testing guide](https://fastapi.tiangolo.com/tutorial/testing/) explains
the `TestClient` approach.

Stage 02 tests also construct fictional incidents directly in Python: aware
timestamps, inclusive scope, ordering, source and evidence references, explicit
error signals, conflicting exit codes, immutable results and inert log text.
They do not parse external log formats or validate a live collector. Run the
domain subset with `uv run --locked pytest tests/test_domain.py`.

Stage 03 adds strict fixture parsing, finite limits, supported redaction rules,
visible collection gaps and CLI exit-code checks. Run its subset with:

```bash
uv run --locked pytest tests/test_fixtures.py tests/test_redaction.py tests/test_reporting.py tests/test_cli.py
```

CLI subprocess tests run without site packages or web configuration. Invalid
input must produce exit `2`, empty stdout and safe stderr. Both
[fictional fixtures](../../fixtures/stage03/) are exercised through reporting.

Stage 04 tests never contact a real Docker daemon. They start a fake Engine API
server on a short Unix socket path under `/tmp` ([docker_fake.py](../../tests/docker_fake.py)),
which records every request, and run the lab process locally against a closed
port. Run the subset with:

```bash
uv run --locked pytest tests/test_docker_engine.py tests/test_docker_lab.py tests/test_collect_cli.py
```

They cover the request guard, client surface, log stream parsing, byte and time
limits, missing or unavailable daemons, label checks, gaps, redaction, inert
log text and reproducible reports. A live lab run is a separate check recorded
in [VERIFICATION.md](../VERIFICATION.md).

Ruff checks common errors and imports; its format check does not modify files.
Strict mypy checks annotated Python types. Pytest verifies behavior. The local
documentation checker validates repository file targets; it does not establish
that external links, heading anchors or their content remain valid.

## Manual HTTP smoke check

Start Uvicorn using the [quickstart](quickstart.md), then request `/health`,
`/docs`, `/redoc` and `/openapi.json` at port 8004. Record this separately from
in-process tests. If a sandbox denies a listening socket, report that
environment restriction instead of calling it an application failure or
claiming a successful HTTP run.

## Record the results

Update [VERIFICATION.md](../VERIFICATION.md) and
[verification.json](../guide/verification.json) when factual results change.
Review and commit/push each completed stage after its checks, then inspect the
remote CI for the pushed commit; see [CI and stage publication](ci-and-delivery.md).
