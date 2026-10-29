# Saved investigations and deployment history

[← README](../../README.md) · [All documentation](../../README.md#documentation)

Stage 05 adds local SQLite records and optional deployment evidence from one
fictional Git lab. The [Russian guide](../STAGE_05.md) explains the full flow.

## Create the lab and save an investigation

Run from the project root. The destination for `create` must not already exist:

```bash
uv sync --locked
uv run --locked python lab/stage05/make_history.py create var/stage05/training-api
uv run --locked python -m app.cli fixtures/stage05/after_deployment.json --repository var/stage05/training-api --store var/stage05/investigations.sqlite3
```

`make_history.py` is a developer setup tool that writes only the lab repository.
The investigator only reads Git. Without `--store`, the CLI prints its report as
before; with `--store`, it prints a receipt containing `investigation_id`,
`created`, hashes, storage time and summary fields. The collector CLI also accepts
`--repository PATH` and `--store PATH`.

The same canonical prepared input returns the original record with
`created: false`. Changing the input creates another record; time of saving is
not part of input identity. Keep the local repository, SQLite file and any
receipts under ignored `var/`; do not commit real evidence.

## Read and verify

List summaries, then copy the `investigation_id` for `ID`:

```bash
uv run --locked python -m app.investigations list --store var/stage05/investigations.sqlite3
```

`show ID --store PATH` prints the exact stored report. `verify ID --store PATH`
checks hashes and rebuilds it from stored input using current code. For example,
after assigning the copied ID to a shell variable `id`:

```bash
uv run --locked python -m app.investigations show "$id" --store var/stage05/investigations.sqlite3
uv run --locked python -m app.investigations verify "$id" --store var/stage05/investigations.sqlite3
```

All three commands open an existing store read-only. `list` defaults to 20 and
accepts `--limit 1..500`. Verification exits `0` for `verified`, `1` for failed
consistency/reproduction checks, and `2` for command/store errors. Other successful
commands exit `0`; rejected input or store access exits `2` with safe stderr.

## Deployment evidence

The allowlist fixes repository identity `training-api-lab`, its pinned root,
service `training-api`, environment `lab` and `deploy/lab/` tag prefix. The
developer chooses its path. Annotated numeric deployment tags must carry the
exact record JSON with an explicit aware `deployed_at`. Commit time is never
used as deployment time. Missing history becomes a gap; an out-of-scope repository
or service is rejected.

Default content limits: 100 tags by name, 4,096 bytes per tag object, 65,536 bytes
per commit object and 2,048 retained message characters after redaction.
Skipped/malformed records and overflow are visible as gaps. History is restricted
to the incident interval. The lab identity check is not authentication, and this
reader is not a sandbox for arbitrary repositories or a hard runtime bound.

## Storage boundaries

Each stored input/report document is limited to 8 MiB. Records contain canonical
sanitized evidence, quality metadata, input/report versions, SHA-256 hashes and
the original report. The table rejects replacement, update and delete through
normal SQL. Hashes and triggers do not protect against an owner rewriting the
database. Reproduction uses the current renderer; an unsupported version or a
changed renderer can fail verification while the original report stays stored.

No database server, storage HTTP endpoint, model or automatic fix is added.
See [ADR 0003](../adr/0003-immutable-investigation-records.md),
[ADR 0004](../adr/0004-recorded-lab-deployments.md) and actual
[verification](../VERIFICATION.md).
