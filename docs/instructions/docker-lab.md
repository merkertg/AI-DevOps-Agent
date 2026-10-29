# Docker lab and collector

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Stage 04 runs one disposable fictional service in Docker and collects its state
and bounded logs into the same JSON report as the
[offline investigation](offline-investigation.md). The
[Russian guide](../STAGE_04.md) explains each step. The access boundary is
recorded in [ADR 0002](../adr/0002-docker-lab-collector-access.md).

## Requirements

Docker Engine 25.0 or newer (API 1.44) on a local Unix socket, for example
Docker Desktop. The first `up` pulls `python:3.12-slim`. Tests do not need Docker.

## Run the lab and collect

From the repository root:

```bash
docker compose -f lab/stage04/compose.yaml up -d
uv run --locked python -m app.collect --minutes 30
docker compose -f lab/stage04/compose.yaml down
```

The lab container `aidevops-lab04-api` has no network, runs as user 65534 with
a read-only filesystem and no capabilities, and never restarts. It writes a
structured startup line, a line with a fictional `token=` value, a line that
resembles an instruction, and a structured `connection_refused` line, then
exits with code `3`. No lab service mounts the Docker socket.

The expected report has `origin: "docker_lab"`, five observations (four log
lines and one `container_exit`), two facts, two unconfirmed hypotheses,
`redaction_count: 1` and `declared_complete` status. Recreate the container with
`up -d --force-recreate` if it ran outside the window. Actual live results are
recorded in [VERIFICATION.md](../VERIFICATION.md).

## Options

| Option | Meaning |
| --- | --- |
| `--minutes N` | Window ending at collection time, `1..1440`, default `30` |
| `--container NAME` | Allowlisted container to read; repeatable; default all |
| `--incident-id ID` | Report identifier slug; default `stage04-<UTC time>` |
| `--docker-socket PATH` | Unix socket; otherwise `DOCKER_HOST=unix://…`, then `/var/run/docker.sock`, then `~/.docker/run/docker.sock` |
| `--repository PATH` | Optional allowlisted Stage 05 Git lab to read deployment evidence |
| `--store PATH` | Optional local SQLite file; save the investigation and print a receipt |

TCP, TLS and SSH Docker hosts are refused.
The [saved investigation guide](investigation-history.md) explains repository
identity, storage and read-only `list`, `show`, `verify` commands. Without these
options the collector retains its report-only behavior.

## What the collector reads

- Allowlist in code (`STAGE04_LAB`): lab `stage04`, service `training-api`,
  container `aidevops-lab04-api`. Other names are refused before connecting.
- Requests: `GET /v1.44/containers/{name}/json` and
  `GET /v1.44/containers/{name}/logs` with `stdout`, `stderr`, `timestamps`,
  `since`, `until` and `tail` only. A single guard refuses every other method,
  path and query parameter. The client exposes only `inspect()` and `logs()`.
- The container must have exactly the requested name and the label
  `ai-devops-agent.lab=stage04`; otherwise collection stops with exit `2`.
- Kept from inspect: status, exit code, OOM flag, restart count, finish time,
  TTY flag and the lab label. Environment, mounts, command, network settings
  and other labels are discarded.

## Mapping to evidence

Each container gets sources `<name>-log` (`application_log`) and `<name>-state`
(`container_state`). A finished container with an exit code in `0..255` and a
finish time inside the window becomes `container_exit`; any other state becomes
a `note` at collection time. Docker state shows only the latest exit.

Log lines use Docker receipt timestamps as event time and collection time as
received time. A line becomes `connection_refused` only when the whole line is
a JSON object without duplicate keys whose `event` field is exactly
`connection_refused`. Everything else is a `note`. Logs can never produce an
exit event. Text is redacted with the Stage 03 patterns, then truncated.

## Limits and gaps

| Resource | Limit | On overflow |
| --- | --- | --- |
| Containers per run | 4 | Refuse |
| Window | 1..1440 minutes | Refuse |
| Log lines per container | 500 newest (`tail=501` detects more) | Drop older lines, record a gap |
| Logs response | 1 MiB | Stop reading, record a gap |
| Inspect response | 256 KiB | Fail with exit `2` |
| Request time | 5 s per socket operation and body deadline | Fail with exit `2` |
| Message | 2,048 characters after redaction | Truncate and record |

A missing container, a container removed before its logs are read, lines without
timestamps, lines outside the window and invalid exit codes are recorded in
`data_quality.gaps`; the report becomes `partial` with exit `0`.

## Exit codes

Exit `0` means a report was produced, including partial evidence. Exit `2`
means the request was refused or Docker could not be read: stdout is empty and
stderr has a static message that does not repeat paths, arguments or daemon
response bodies.

## Boundary

The guard prevents the collector's code from sending mutating requests; it does
not reduce the privileges of the Docker socket held by the process. Use the
collector only on your own machine against this lab. It is not exposed to HTTP,
an agent or a model. Run the [verification commands](testing.md); the Docker
tests use a fake daemon on a Unix socket.
