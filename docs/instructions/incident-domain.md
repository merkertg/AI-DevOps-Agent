# Incident domain

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Stage 02 provides pure Python incident investigation on fictional, already
structured observations. Import the public API from `app.domain`; the
[Russian guide](../STAGE_02.md) includes a complete runnable example.

## Input contracts

| Type | Fields and meaning |
| --- | --- |
| `Incident` | `incident_id`, `service`, `starts_at`, `ends_at`: one service and an inclusive event-time interval |
| `Source` | `source_id`, `service`, `kind`: `SourceKind.APPLICATION_LOG` or `SourceKind.CONTAINER_STATE` |
| `Observation` | `observation_id`, `source_id`, `event_time`, `received_at`, `kind`, optional `message` and `exit_code` |
| `EvidenceReference` | `observation_id`: an existing observation in this investigation |

Use enum members rather than raw strings. Observation kinds are
`CONNECTION_REFUSED`, `CONTAINER_EXIT` and `NOTE`; a container exit needs an
integer exit code in `0..255`, excluding booleans. Connection refusal requires
an application-log source; container exit requires a container-state source.
Timestamps must carry an explicit UTC offset. They normalize
to UTC. `received_at` may fall after the incident end but cannot precede
`event_time`.

## Investigation

`investigate(incident, sources, observations)` creates an immutable
`Investigation` with `sources`, `timeline`, `facts`, `hypotheses` and
`contradictions`. Reordering the same input does not change the result. Duplicate
IDs, unknown sources, sources for another service, events outside the incident,
invalid times and unresolved evidence references raise `ValueError`.

The timeline sorts by `(event_time, received_at, source_id, observation_id)`;
sources sort by `source_id`. `Fact`, `Hypothesis` and `Contradiction` each have
`code`, `statement` and an immutable `evidence` tuple of `EvidenceReference`.

The rules recognize explicit connection-refusal observations and nonzero
container exits. Facts report observations; hypotheses suggest possible causes
without confirming them. Conflicting exit codes for the same source and event
instant remain in the timeline and produce a contradiction. A causal hypothesis
for that conflicting exit group is suppressed. Exit code zero produces an
observed-exit fact without an error hypothesis. A container-state source must
represent one entity, such as one container.
Each finding carries references to its supporting observations.

## Boundaries

Log messages are inert strings. They neither supply executable instructions nor
trigger error rules merely by containing error-like prose. The domain does not
read files, inspect Docker, change the host, call a model or generate a CLI/HTTP
report. Its reference checks establish that evidence exists, not that arbitrary
external claims are true. Stage 03 adds file limits, parsing, redaction and CLI
reporting in separate layers; see [offline investigation](offline-investigation.md).
Direct domain calls still expect already structured inputs. Use fictional data.
