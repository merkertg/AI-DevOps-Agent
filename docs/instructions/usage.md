# Using the API

[← README](../../README.md) · [All documentation](../../README.md#documentation)

The health contract, the Stage 08 investigation routes, access tokens and
configuration. Start the service with the [quickstart](quickstart.md) first.
The access decision is recorded in
[ADR 0007](../adr/0007-token-scoped-api-and-container.md).

## Health check

```bash
curl -i http://127.0.0.1:8004/health
```

HTTP `200`, default JSON:

```json
{"status":"ok","service":"ai-devops-agent"}
```

The strict `HealthResponse` contract has exactly two fields: `status` is the
literal `"ok"`; `service` is the configured service name. This endpoint checks
only that the API answers. It does not inspect Docker or an incident.

## Routes

| Request | Expected result |
| --- | --- |
| `GET /health` | `200`, health JSON |
| `POST /health` | `405`, unsupported method |
| `GET /` | `404`, no homepage |
| `GET /docs` | `200`, Swagger UI HTML |
| `GET /redoc` | `200`, ReDoc HTML |
| `GET /openapi.json` | `200`, OpenAPI contract |
| `POST /investigations` | Write token: save an inline fixture; `201` new, `200` same input |
| `GET /investigations?limit=N` | Read token: newest summaries, `1..100`, default `20` |
| `GET /investigations/{id}` | Read token: the stored report, byte for byte |
| `GET /investigations/{id}/verification` | Read token: hash and reproduction checks |
| `POST /investigations/{id}/agent` | Read token: run the offline agent; nothing is stored |

The documentation HTML may reference external browser resources. Rendering its
interface is separate from serving the JSON API.

## Configuration

| Variable | Default | Validation |
| --- | --- | --- |
| `APP_NAME` | `ai-devops-agent` | String, trimmed, nonempty |
| `APP_ENV` | `development` | `development`, `test` or `production` |
| `APP_DEBUG` | `false` | Boolean; keep disabled for normal use |
| `APP_READ_TOKEN_SHA256` | unset | 64 lowercase hex digits: SHA-256 of the read token |
| `APP_WRITE_TOKEN_SHA256` | unset | 64 lowercase hex digits: SHA-256 of the write token |
| `APP_STORE_PATH` | `var/investigations.sqlite3` | SQLite store used by the API |

For one zsh/Bash command:

```bash
APP_NAME=devops-training uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8004
```

The response now contains `"service":"devops-training"`. Explicit factory
settings override process variables, then `.env`, then defaults. Settings
loading is cached for the process. Restart to apply changes. `APP_ENV` is a
validated environment label, not a deployment or authorization mechanism.

## Investigation API

The investigation routes are closed until at least one token digest is set:
every request then gets `403`. Configure digests, never plain tokens. Use a
private terminal, because the token appears in shell history:

```bash
printf %s "$WRITE_TOKEN" | shasum -a 256
```

Clients send `Authorization: Bearer <token>`. The write token can save and
read; the read token can only read and run the agent. Save the Stage 05 fixture
and read it back:

```bash
curl -s -X POST http://127.0.0.1:8004/investigations -H "Authorization: Bearer $WRITE_TOKEN" -H "Content-Type: application/json" --data-binary @fixtures/stage05/after_deployment.json
curl -s http://127.0.0.1:8004/investigations/inv-3dec28b1f8811404869dc78b -H "Authorization: Bearer $READ_TOKEN"
```

The identifier comes from the input hash, so the same body always names the
same record; a repeat answers `200` with `"created": false` and the original
storage time. The body is the [fixture format](offline-investigation.md) as
`application/json`, at most 1 MiB. The API never reads a server path, Docker,
Git or Prometheus for a client; those collectors remain host CLIs.

| Status | Meaning |
| --- | --- |
| `400` | The body is not a valid fixture |
| `401` | Missing or unknown bearer token (`WWW-Authenticate: Bearer`) |
| `403` | API not configured, or the token lacks the write scope |
| `404` | Unknown or malformed investigation ID |
| `409` | Stored record failed integrity or verification |
| `413` / `415` | Body over 1 MiB / not `application/json` |
| `503` | Store unreadable or not writable |

Errors are JSON `{"detail": "..."}` with static text; request values are not
repeated. The [smoke script](../../scripts/api_smoke.py) runs the whole path
against a running server.

## Command-line features

Storage, deployment history, the agent and telemetry are also CLI features:
[saved investigations](investigation-history.md),
[bounded agent](bounded-agent.md) and [telemetry](telemetry.md). The API does
not expose `--repository`, `--prometheus`, `--metrics-file` or `--traces`.
