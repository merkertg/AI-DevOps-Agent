# CI and stage publication

[← README](../../README.md) · [All documentation](../../README.md#documentation)

What the GitHub Actions workflow checks and how a completed stage is published.

The `CI` workflow runs for pushes to `main`, pull requests targeting `main` and
manual dispatch. One job, `Lint, types, tests and documentation`, runs on
`ubuntu-24.04` with a 10-minute timeout:

```text
Checkout without persisted credentials + uv 0.12.23 + Python 3.12
                                  ↓
uv sync --locked → Ruff check → Ruff format --check → strict mypy → pytest → check_docs.py
```

No database service, Docker daemon, LLM key or production secret is required;
Stage 04 tests use a fake Docker daemon on a Unix socket inside the test process.
The workflow token can only read repository contents, third-party actions are
pinned to full commit SHAs, and a newer run for the same ref cancels the older
one. The uv integration follows the official
[GitHub Actions guide](https://docs.astral.sh/uv/guides/integration/github/).
The workflow file is configuration; a successful remote run must be
established separately.

## Publish a completed stage

Each completed stage is committed and pushed, as requested for this project:

1. Finish the current stage and run the [local checks](testing.md).
2. Update its walkthrough, affected instructions, development log and verification.
3. Review the diff, including ignored local files and accidental sensitive data.
4. Create a focused English commit and push it to the configured remote branch
   without force. Recheck the remote branch first; if it diverged, reconcile it
   without discarding anyone's work.
5. Confirm the remote branch points to that commit and inspect its GitHub Actions
   run. Record the commit and remote result in the delivery report.

`git commit` alone does not start a remote workflow. A successful push confirms
publication of Git history; it does not establish a passing CI run. A run that is
queued or executing remains `pending`. If remote results cannot be accessed,
record `not_verified`. Implementation, local checks, push and remote CI each
have their own status.

## Delivery boundaries

Stages 01–05 provide continuous integration configuration. Container building, image
publication and deployment are not configured. Stage 08 will add container
checks and a documented delivery path after the investigation workflow exists.
Delivery of this project's code is separate from the agent's read-only access
to systems under investigation.

**[Detailed CI/CD guide in Russian](../CI_CD.md)** explains the terms, triggers,
step order, permissions, local reproduction and troubleshooting. Actual results
belong in [VERIFICATION.md](../VERIFICATION.md).
