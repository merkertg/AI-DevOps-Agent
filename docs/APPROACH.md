# Approach

[README](../README.md) · [Roadmap](ROADMAP.md) · [Architecture decision](adr/0001-staged-read-only-incident-investigation.md)

## Product scenario

An engineer investigates one fictional incident affecting a small Docker
application. The eventual system will combine logs, container observations,
deployment metadata and telemetry for a stated time window. Its report will
separate observed facts, deterministic findings, possible causes, missing
evidence and suggested checks. A recent deployment is context, not proof that it
caused an error.

The first useful investigation will use local fictional fixtures. A complete
offline path comes before live Docker access or a model provider. This gives
each new adapter a stable contract and makes regressions reproducible.

## Implemented foundation

Stage 01 contains a small FastAPI application:

```text
client → Uvicorn → FastAPI → health handler → HealthResponse → JSON
                         ↑
                  create_app(Settings)
```

`app/config.py` validates settings and caches their normal process-level load.
`create_app()` accepts explicit settings so tests can construct independent
applications. `app/main.py` owns the application factory, response contract and
health handler. The response contains only `status` and `service`.

`GET /health` is liveness only. It does not claim that a Docker host, investigated
application, telemetry source or model is healthy. These integrations do not
exist at this stage. Tests use in-process clients and isolated settings.

## Planned architecture

Grow a modular monolith as behavior appears:

| Layer | Planned responsibility | Added from |
| --- | --- | --- |
| Domain | Incident scope, evidence references, timeline and deterministic findings | Stage 02 |
| Application | Investigation use cases, input/output ports and report generation | Stage 03 |
| Infrastructure | Bounded file adapters, Docker collection, persistence, Git and model adapters | Stages 03–06 |
| Agent orchestration | Bounded LangGraph investigation over application tools | Stage 06 |
| API | Validated requests and reports exposed through application use cases | Stage 08 |

Domain code will depend on ordinary Python types rather than FastAPI, Docker,
GitPython, LangGraph or storage frameworks. Adapters translate external data into
that domain. Future directories are not created as empty placeholders.

## Evidence and model boundaries

Each observation will retain its source, observation time, collection window and
stable evidence reference. Findings must be reproducible from the same normalized
input. Reports will distinguish event time from collection time and state when
data is missing, stale or truncated.

Before external collection, input limits and redaction will bound size, time
range and output volume. Secrets and personal data in logs must be considered
before persistence or model calls. Redaction will have explicit supported
patterns and tests; it will not be presented as a guarantee that arbitrary logs
contain no secrets.

The model will receive only allowlisted read-only tools with validated arguments.
It may propose an evidence-linked hypothesis or a further diagnostic check.
Deterministic code will enforce incident scope, tool budgets and source
references. Log text is untrusted data, including text that looks like an
instruction to the agent. Missing or conflicting evidence must remain visible.

There will be no model tool for shell execution, container exec, restart,
deployment, file modification or automatic repair. Docker access will be added
through a deliberately restricted collector in a disposable lab. A read-only
application method alone is not a permission boundary for an unrestricted Docker
socket. The adapter design must address the daemon access it actually receives;
the [Docker Engine API](https://docs.docker.com/reference/api/engine/) exposes the
underlying daemon operations.

## Learning and delivery

Every stage needs a working result, meaningful checks, a Russian walkthrough,
updated usage instructions and factual verification. The stage is then committed
and pushed, as requested for this project. Check the remote ref and the CI result
for that commit separately. A failed push or pending CI is recorded explicitly.

Python 3.12, uv and `uv.lock` make the foundation reproducible. `--locked` makes
uv fail when project metadata requires a lock change instead of updating it
silently; see [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).
Runtime dependencies for later stages are introduced with the corresponding
implementation, rather than installed in anticipation.
