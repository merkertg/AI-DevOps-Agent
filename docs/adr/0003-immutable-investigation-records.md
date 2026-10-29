# ADR 0003: Immutable local investigation records

- Status: accepted
- Date: 2026-10-29

## Context

Stage 05 must preserve an investigation after its process exits, reproduce its
report from the exact prepared evidence and handle repeated saves predictably.
The project currently runs as a local learning CLI. A server database would add
an external service before concurrent API users require it; independent JSON
files would require custom indexing, atomic deduplication and update controls.

## Decision

Use the standard-library SQLite adapter behind the `InvestigationStore` protocol.
One local file contains a versioned `investigations` table, canonical prepared
input JSON, the exact rendered report, both document versions, SHA-256 hashes
and a summary. The input is the normalized, sanitized evidence and its quality
metadata; it is not a copy of the raw fixture or Docker response.

Use the full input hash as the unique deduplication key. Derive the public ID
as `inv-` plus its first 24 lowercase hex characters. An atomic save inserts once
or returns the original record for the same input, including its original
storage time. An identifier collision for a different full hash is an error.
Triggers reject replacement, update and deletion through normal SQL operations.

Open list, show and verify commands in SQLite read-only mode. Before storing,
require consistent documents and reject text that the supported redaction
filter would still change. Bound each input/report document at 8 MiB. Verification
checks hashes, ID, document decoding, metadata and exact report reproduction
using the current application code. Showing an intact saved report does not
recompute it from newer collection or history.

## Consequences

SQLite needs no database server and makes an append atomic. A writable process
creates the requested parent directories and file; new files use private local
permissions. The file remains a local artifact under ignored `var/` by convention.
No migration, backup/restore service, retention policy or multi-user API is added.

Append-only triggers and hashes provide consistency checks, not authentication
or tamper resistance against a user who controls the file. That user can replace
the database, remove triggers or rewrite documents and hashes together.
The application version and storage time are metadata, not signed evidence.
The redaction filter only covers documented patterns; fictional data remains
required.

Reproduction is tied to the current renderer and supported snapshot version.
A later code change can make verification report a mismatch while the original
stored report remains available. Changing historical evidence requires a new
prepared input and a new record, rather than editing an existing investigation.
