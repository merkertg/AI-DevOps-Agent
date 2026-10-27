# Using the API

[← README](../../README.md) · [All documentation](../../README.md#documentation)

The Stage 01 health contract, available routes and configuration. Start the
service with the [quickstart](quickstart.md) first.

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

The documentation HTML may reference external browser resources. Rendering its
interface is separate from serving the JSON API.

## Configuration

| Variable | Default | Validation |
| --- | --- | --- |
| `APP_NAME` | `ai-devops-agent` | String, trimmed, nonempty |
| `APP_ENV` | `development` | `development`, `test` or `production` |
| `APP_DEBUG` | `false` | Boolean; keep disabled for normal use |

For one zsh/Bash command:

```bash
APP_NAME=devops-training uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8004
```

The response now contains `"service":"devops-training"`. Explicit factory
settings override process variables, then `.env`, then defaults. Settings
loading is cached for the process. Restart to apply changes. `APP_ENV` is a
validated environment label, not a deployment or authorization mechanism.

## Not implemented yet

There are no investigation, upload, container, agent or report endpoints yet.
Their staged implementation is described in the [roadmap](../ROADMAP.md).
