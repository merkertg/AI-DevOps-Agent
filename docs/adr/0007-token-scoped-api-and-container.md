# ADR 0007: Token-scoped investigation API, evaluations and container delivery

- Status: accepted
- Date: 2026-10-30

## Context

Stage 08 exposes the investigation workflow over HTTP, evaluates its results
and packages it. Reports can contain operational details, so an open endpoint
would publish them to anyone who reaches the port. An API that accepts paths,
URLs or socket names would let clients make the server read files, Git
repositories, Docker or Prometheus. Passing tests do not show that results are
right, and a self-produced reference cannot catch the code's own mistakes.

## Decision

- **Closed by default.** Investigation routes answer `403` until at least one
  token digest is configured. `APP_READ_TOKEN_SHA256` and
  `APP_WRITE_TOKEN_SHA256` hold SHA-256 digests, never tokens. Clients send a
  bearer token; digests are compared in constant time. The write token saves
  and reads; the read token reads, verifies and runs the agent. `/health`
  stays public and makes no readiness claim.
- **Inline evidence only.** `POST /investigations` accepts one fixture
  document of at most 1 MiB as `application/json` and parses it with the
  Stage 03 rules. No route accepts a server path, repository, socket or URL;
  Docker, Git and telemetry collection remain host CLIs.
- **Repeatable requests.** The record ID is derived from the canonical input,
  so the same body returns the original record with `200`; a new one answers
  `201` with a `Location` header. Reads return stored bytes after an integrity
  check; the agent runs only on records that verify.
- **Static errors.** Every error is `{"detail": ...}` with fixed text and does
  not repeat request values.
- **Independent evaluations.** Hand-written labels of six fictional incidents
  state the expected facts, hypotheses, contradictions, required and forbidden
  evidence and data quality. The suite scores reference validity, recall,
  unexpected findings, unknown-cause handling and agent compliance, and must
  detect ten deliberate corruptions of every result. It runs in CI.
- **Container.** A two-stage image pinned by digest installs runtime
  dependencies from `uv.lock`, runs the API as UID 10001 and stores data on
  `/data`. CI builds it, checks the user and the absence of development tools,
  and runs the smoke script with tokens created for that run. No image is
  pushed and nothing is deployed.

## Consequences

The full user path works from a clean checkout and from the image, and a
regression in findings or agent behavior fails CI rather than passing
silently. Bearer tokens are only as safe as the transport: the API must stay
on loopback or behind TLS. There is one read and one write token, no user
identity, rotation or audit trail, and any reader sees every report.

Evaluation labels cover six small fictional incidents; passing them does not
show quality on real incidents, and agreement with labels written by the same
author is weaker than an external review. Registry publication, signing,
scanning and deployment need their own decision with an owner and credentials.
