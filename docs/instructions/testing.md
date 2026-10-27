# Testing and verification

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Lint, formatting, strict type checks, tests, documentation links and a manual
HTTP smoke check.

## Verify

Run from the repository root:

```bash
uv sync --locked
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
