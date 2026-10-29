# ADR 0004: Recorded deployments from the allowlisted Git lab

- Status: accepted
- Date: 2026-10-29

## Context

A commit timestamp states when a revision was committed; it does not establish
when or whether that revision ran. Stage 05 needs explicit deployment evidence
without giving the investigator a general Git command or write capability.

## Decision

Use GitPython to read annotated tags from the fictional `training-api-lab`
repository. The code allowlist fixes its root commit, service `training-api`,
environment `lab` and namespace `deploy/lab/`. A developer supplies the local
path, and the reader checks the expected root object before reading deployment
tags. Changing the allowed identity requires a code change.

Each accepted tag has a numeric sequence leaf and a JSON message with exactly
`record`, `version`, `service`, `environment` and `deployed_at`. The record type is
`ai-devops-agent.deployment`, version `1`. The explicit aware deployment time
must follow the target commit time and not follow the tag recording time.
Only deployment times inside the incident interval become observations. Commit
and tag times remain contextual fields, never substitutes for `deployed_at`.

Read tag and commit objects with finite content limits. Reject redirected Git
environments, unsupported linked-worktree/alternate/reftable layouts and
repositories outside the identity convention. Missing or unreadable history
becomes an explicit gap. No checkout, reset, pull, fetch, commit, tag creation
or push is part of the investigation reader. The separate `make_history.py`
script writes the disposable lab only when the developer runs setup.

Add a `DEPLOYMENT_HISTORY` source and `DEPLOYMENT` observations to the existing
domain. A rule may link the most recent preceding deployment to a later failure
signal, with both evidence references and an explicit statement that temporal
order does not establish causation.

## Consequences

The pinned root object identifies this lab and its copies by convention. It is
not a signature, provenance verification or proof that all target revisions
descend from that root. Tag messages and commit summaries remain untrusted data;
only exact structured metadata is accepted, and retained text is sanitized.

This is a local developer lab reader, not a sandbox for hostile repositories.
The tag/object limits bound retained evidence and object contents; they do not
establish a hard end-to-end runtime or bound every internal GitPython ref scan.
Unsupported repository layouts are reported explicitly. Wider use requires a
separate access and resource-isolation decision.

The tags are recorded deployment claims. They do not prove what actually ran,
and a preceding deployment is only a possible investigative lead. Model
orchestration and live deployment verification remain later work.
