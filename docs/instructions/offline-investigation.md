# Offline investigation

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Stage 03 reads one bounded fictional JSON fixture, normalizes and sanitizes it,
then prints a deterministic JSON report. It needs no server, `.env`, credentials,
Docker or network access. The [Russian guide](../STAGE_03.md) explains each step.

## Run

From the repository root:

```bash
uv sync --locked
uv run --locked python -m app.cli fixtures/stage03/connection_failure.json
uv run --locked python -m app.cli fixtures/stage03/partial_incident.json
```

The first example produces three timeline observations, two facts, two
unconfirmed hypotheses and no contradictions. Its data-quality status is
`declared_complete`, a producer claim. The second has one observation, one fact,
one hypothesis, sanitized collection gaps, an uncovered container source and
`partial` status. Both include unknown circumstances and limitations.

Exit `0` means a report was produced, including for partial evidence. Exit `2`
means input or invocation was rejected; stdout is empty and stderr contains a
safe diagnostic. Without `--store`, the CLI does not write a report file itself. If needed, redirect
stdout to an ignored local location such as `var/report.json` after creating
`var/`. Do not commit real operational inputs or generated private reports.

Stage 05 adds optional `--repository PATH` deployment evidence and `--store PATH`
local SQLite storage. With `--store`, stdout is a receipt rather than the full
report. See [saved investigations](investigation-history.md) for the full path.

## Fixture schema version 1

See the complete [fictional fixture](../../fixtures/stage03/connection_failure.json).
Only the following fields are accepted:

| Object | Required fields | Optional fields |
| --- | --- | --- |
| Top level | `schema_version` (integer `1`), `incident`, `sources`, `observations` | `collection` |
| `incident` | `incident_id`, `service`, `starts_at`, `ends_at` | — |
| Each source | `source_id`, `service`, `kind` | — |
| Each observation | `observation_id`, `source_id`, `event_time`, `received_at`, `kind` | `message` (default empty), `exit_code` |
| `collection` | — | `complete` (boolean), `truncated` (boolean, default false), `gaps` (text array, default empty) |

Missing `collection.complete` means unknown completeness. Identifiers and service
names are trimmed ASCII slugs matching `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`;
recognized sensitive metadata is rejected rather than echoed or rewritten.
Times are trimmed ISO-8601 strings with explicit offsets and normalize to UTC.
Source kinds are `application_log`, `container_state` or `deployment_history`;
observation kinds are `connection_refused`, `container_exit`, `deployment` or
`note`. Enum values must match exactly. A deployment requires a deployment-history
source; a structured fixture record remains a producer claim.
Exit codes are integers in `0..255`, excluding booleans, and only accompany exits.
The [domain contract](incident-domain.md) enforces scope and source compatibility.

The input must be a regular UTF-8 file. A final-component symbolic link,
nonregular file, BOM, invalid Unicode, duplicate JSON key, unknown field,
unsupported schema, floating-point/nonfinite number or invalid domain evidence
is rejected. Plain log text has no parser at this stage; text messages live
inside structured observations.

## Fixed CLI limits

| Resource | Limit | On overflow |
| --- | --- | --- |
| Input bytes | 1,048,576 (1 MiB) | Reject before parsing |
| Sources | 32 | Reject |
| Observations | 1,000 | Reject |
| Incident duration | 86,400 seconds (24 hours) | Reject |
| Collection gaps | 32 | Reject |
| Each sanitized message or gap | 2,048 characters | Truncate after redaction and record it |

The file reader reads at most the byte limit plus one byte, checks the open
file type and never waits on a FIFO. Collection timestamps can follow the
incident window; event timestamps must remain inside its inclusive bounds.
`InputLimits` supports smaller positive bounds in Python tests; the CLI exposes
no option to disable its limits.

## Supported redaction

Messages and collection gap descriptions are filtered before text truncation.
The marker is `[REDACTED]`. The supported patterns cover:

- Case-insensitive `password`, `passwd`, `token`, `access_token`, `api_key`,
  `api-key`, `apikey`, `secret`, `client_secret` assignments using `:` or `=`;
  quoted values can contain spaces, unquoted values are token-shaped.
- `Bearer` credentials matching the contiguous character set `[A-Za-z0-9._~+/=-]+`.
- URL userinfo (`scheme://user:password@host`) as a whole.
- Recognizable `sk-`, `sk-proj-`, `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` and
  `github_pat_` token shapes with at least eight suffix characters.
- Supported ASCII email addresses, including common surrounding punctuation.

`redaction_count` counts replacements, not independent secrets or a privacy
score. Arbitrary personal names, IP addresses, encoded values, unknown key names
and unsupported secret formats can remain. The filter does not establish that
arbitrary real logs are safe for storage or model input. Examples and tests use
fictional values.

## Read the report

`origin` is `fixture` for this CLI; the [Docker lab collector](docker-lab.md)
produces `docker_lab` and adds its own limitations. `timeline` contains
sanitized observations. Each `facts`, `hypotheses` and
`contradictions` entry includes `evidence` references: locate the matching
`observation_id` in the timeline. The report retains UTC event and received
times, source metadata and structured signals.

`data_quality` exposes the producer's declarations, sanitized gaps, truncated
observation IDs, truncated gap count, redaction count and sources with no
observations. Its `status` is `partial` when any known gap, truncation, empty
timeline, uncovered source or contradiction exists. Otherwise it is
`declared_complete` for a complete declaration and `unknown` without one.
`unknowns` always states that root cause is unconfirmed. Log messages remain
inert data even when they resemble commands.

Run the [verification commands](testing.md); actual results are recorded in
[VERIFICATION.md](../VERIFICATION.md).
