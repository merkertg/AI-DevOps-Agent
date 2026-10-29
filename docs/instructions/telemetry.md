# Telemetry: metrics, traces and correlation

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Stage 07 adds allowlisted Prometheus metrics and an OpenTelemetry trace export
to either investigation CLI. Logs, spans and metric signals are linked by
service, time and trace ID; missing trace IDs, delayed or stopped metrics,
clock skew and unavailable sources become gaps. The
[Russian guide](../STAGE_07.md) explains each step; the boundary is in
[ADR 0006](../adr/0006-allowlisted-telemetry.md).

## Offline example

```bash
uv run --locked python -m app.cli fixtures/stage07/incident.json --metrics-file fixtures/stage07/metrics.json --traces fixtures/stage07/traces.jsonl
```

The fictional incident has three refused connections with trace IDs, three
matching error spans, two metric signals and a container exit. The report has
three `same_trace` facts and one gap: the failure counter stopped reporting
after the process exited. Add `--store PATH` to save it; the
[agent](bounded-agent.md) can then cite the trace facts.

## Live lab

```bash
mkdir -p var/stage07/telemetry
docker compose -f lab/stage07/compose.yaml up -d
docker wait aidevops-lab07-api
uv run --locked python -m app.collect --lab stage07 --minutes 10 --prometheus http://127.0.0.1:19090 --traces var/stage07/telemetry/traces.jsonl
docker compose -f lab/stage07/compose.yaml down -v
```

`docker wait` returns `3` after six refused attempts about five seconds apart.
The service logs each refusal with a trace ID, increments
`training_api_dependency_failures_total` and exports an error span over
OTLP/HTTP. The collector writes JSON lines to `var/stage07/telemetry/`;
Prometheus scrapes the service every 5 s and is published only on
`127.0.0.1:19090`. Lab traffic uses an internal network without outside
access; no service mounts the Docker socket. On Linux, set
`AIDEVOPS_LAB_UID` and `AIDEVOPS_LAB_GID` to your IDs so the collector can write
the export. On macOS, see the
[file sharing note](docker-lab.md#stage-07-telemetry-lab-and-macos-file-sharing).

## Options

| Option | Meaning |
| --- | --- |
| `--prometheus URL` | Loopback `http://host:port` only; other hosts, TLS, paths and credentials are refused |
| `--metrics-file PATH` | Recorded `query_range` responses keyed by allowlisted query name; excludes `--prometheus` |
| `--traces PATH` | OTLP JSON lines export, a regular file that is not a link |

## Allowlisted queries

| Name | PromQL | Signal when |
| --- | --- | --- |
| `dependency_failures` | `increase(training_api_dependency_failures_total{job="training-api"}[1m])` | value `> 0` |
| `target_down` | `1 - up{job="training-api"}` | value `> 0` |

The client sends only `GET /api/v1/query_range` with these exact expressions
and the incident window; no query text comes from arguments, logs or the agent.
Each rise above the threshold becomes one `metric_signal`; a summary note
records sample count, range and maximum.

## Correlation and gaps

| Check | Report |
| --- | --- |
| Log and span share a trace ID | `same_trace` fact citing both |
| Refused connection without a trace ID | Gap listing those log observations |
| Log names a trace absent from the export | Gap: spans may be delayed, sampled or lost |
| Log time outside its spans by more than 1 s | Gap: log and trace clocks may disagree |
| Newest metric sample older than 60 s at window end | Gap: collection delayed or target stopped |
| No data, several series, non-finite values, Prometheus down | Gap per metric or one unavailability gap |
| Missing, linked, oversized or non-UTF-8 export | Gap: traces are unknown |

Signals at the same time are correlated, not causally linked. A deployment
followed strictly later by a metric or span failure may yield the cautious
deployment hypothesis; simultaneous events do not.

## Limits

Up to 500 points per series (the step widens from 15 s for long windows),
1 MiB per response, 5 s per request, 20 signals per query, a 4 MiB export,
10,000 lines, 2,000 spans and 200 error spans. Messages are redacted and
bounded to 2,048 characters.
