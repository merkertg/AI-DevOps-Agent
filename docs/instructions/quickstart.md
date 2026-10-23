# Quickstart

[README](../../README.md) · [Detailed Russian guide](../STAGE_01.md)

Use Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Open a terminal at the repository root, where `pyproject.toml` is located.

```bash
uv sync --locked
uv run --locked python --version
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8004
```

Keep that terminal running. In another terminal, send:

```bash
curl -i http://127.0.0.1:8004/health
```

Expect HTTP `200` and `{"status":"ok","service":"ai-devops-agent"}`.
Open [Swagger UI](http://127.0.0.1:8004/docs) for the interactive API contract.
Stop the server with `Ctrl+C`.

No `.env`, Docker, database or model key is needed. To override settings, create
an untracked `.env` using [.env.example](../../.env.example), preserving any
existing local file. Available variables are `APP_NAME`, `APP_ENV` and
`APP_DEBUG`; see [usage](usage.md). Restart after changing settings.

Port 8004 is a local convention. If it is occupied, stop your earlier project
server or choose another available port and use it in both the command and URL.
`--reload` is for local development. Run [checks](testing.md) before completing
a stage and commit/push the checked result as described in [CI](../CI_CD.md).
