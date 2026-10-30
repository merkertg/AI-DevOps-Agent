# Container delivery

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Stage 08 packages the HTTP API as a container image. The decision is recorded
in [ADR 0007](../adr/0007-token-scoped-api-and-container.md); actual build and
smoke results are in [verification](../VERIFICATION.md).

## Build and run

```bash
docker build -t ai-devops-agent:0.8.0 .
docker run --detach --name ai-devops-agent -p 127.0.0.1:8004:8004 -e APP_READ_TOKEN_SHA256="$READ_DIGEST" -e APP_WRITE_TOKEN_SHA256="$WRITE_DIGEST" -v ai-devops-agent-data:/data ai-devops-agent:0.8.0
SMOKE_READ_TOKEN="$READ_TOKEN" SMOKE_WRITE_TOKEN="$WRITE_TOKEN" uv run --locked python scripts/api_smoke.py --base-url http://127.0.0.1:8004
docker rm --force ai-devops-agent
```

`READ_DIGEST` and `WRITE_DIGEST` are SHA-256 digests of the tokens, as in the
[API instruction](usage.md#investigation-api). The smoke script checks token
scopes, an idempotent save, an unchanged read-back, verification, the agent and
a 404, then prints a summary.

## What the image contains

| Property | Value |
| --- | --- |
| Base | `python:3.12.15-slim`, pinned by index digest |
| Build | uv `0.12.23` (pinned by digest) installs `uv.lock` runtime dependencies only |
| Contents | `.venv` and `app/`; no tests, fixtures, labs, docs, uv or build cache |
| User | `app`, UID/GID `10001`, no login shell |
| Data | `APP_STORE_PATH=/data/investigations.sqlite3` on the `/data` volume |
| Port | `8004`; publish it on `127.0.0.1` unless TLS and access control sit in front |
| Health | `HEALTHCHECK` calls `/health` with the standard library |
| Environment | `APP_ENV=production`; investigation routes stay closed without token digests |

The image runs only the API. It has no `git` executable, Docker client or
access to Prometheus, so `--repository`, the Docker collector and telemetry
options stay host CLIs. Saving and reading investigations of inline fixtures,
verification and the offline agent work inside it.

## Delivery and its limits

CI builds the image for each push, checks the user and the absence of
development tools, runs it with random tokens and runs the smoke script. The
image is not pushed to a registry, signed or scanned, and nothing is deployed;
those steps need a registry, credentials and an owner. Serving the API beyond
your machine also needs TLS, token rotation and a decision about who may read
incident reports. Automatic repair of investigated systems is not part of the
project.
