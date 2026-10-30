# Quickstart

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Install the tools, start the API locally, check the health endpoint and
optionally change settings.

## Requirements

- Python 3.12. The project accepts `>=3.12,<3.13`; `.python-version` selects 3.12.
- A local `git` executable for the optional Stage 05 deployment-history lab.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) for dependency
  and environment management.
- No `.env`, Docker, database or model key is needed for the API, tests and
  offline CLI. The optional [Docker lab](docker-lab.md) needs Docker Engine 25.0+.

Run all commands from the project root, the directory containing `pyproject.toml`.

## Run an offline investigation

```bash
uv sync --locked
uv run --locked python -m app.cli fixtures/stage03/connection_failure.json
```

This prints a JSON report without starting the web server. Input is fictional;
the report retains observations, evidence links and uncertainty. See
[offline investigation](offline-investigation.md) for the format and limits.

## Save an investigation

For saved reports and the fictional deployment-history lab, follow
[saved investigations](investigation-history.md). `--store PATH` adds local SQLite
storage to either investigation CLI; `--repository PATH` reads allowlisted lab
deployment evidence. No database server is required.

## Run the bounded agent

After saving an investigation, run the offline LangGraph agent on its ID as
described in the [bounded investigation agent](bounded-agent.md). It needs no
model, network or credentials.

## Add metrics and traces

The [telemetry](telemetry.md) instruction runs the offline example with recorded
metrics and a trace export, and the optional Stage 07 lab with Prometheus and an
OpenTelemetry Collector.

## Use the investigation API, evaluations and the container

Set token digests to open the investigation routes, as in
[using the API](usage.md#investigation-api). Run the labelled
[evaluations](evaluations.md) with `uv run --locked python -m app.evaluate`.
Build and run the API image as in [container delivery](container.md).

## Start the API

```bash
uv sync --locked
uv run --locked python --version
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8004
```

`uv sync --locked` creates `.venv` from `uv.lock` and fails instead of silently
rewriting the lock file. Keep the server terminal running. `--reload` restarts
the process after Python code changes and is meant for local development.

## Check the response

In another terminal:

```bash
curl -i http://127.0.0.1:8004/health
```

Expect HTTP `200` and:

```json
{"status":"ok","service":"ai-devops-agent"}
```

Open [Swagger UI](http://127.0.0.1:8004/docs),
[ReDoc](http://127.0.0.1:8004/redoc) or
[OpenAPI JSON](http://127.0.0.1:8004/openapi.json). `/health` is a liveness
check: it confirms that this API can respond. It does not check Docker, a model
or an application under investigation. The root path `/` returns `404`.

## Optional settings

Defaults are sufficient to start. To override them, create an untracked `.env`
based on [.env.example](../../.env.example), preserving any existing local file.
Available variables are `APP_NAME`, `APP_ENV` and `APP_DEBUG`; their validation
and precedence are described in [usage](usage.md#configuration). Restart the
server after changing settings.

## Stop the server

Press `Ctrl+C` in the server terminal. Closing the browser page does not stop it.

Port 8004 is a local convention. If it is occupied, stop your earlier project
server or choose another available port and use it in both the command and URL.
Run the [checks](testing.md) before completing a stage and publish the checked
result as described in [CI and stage publication](ci-and-delivery.md).
