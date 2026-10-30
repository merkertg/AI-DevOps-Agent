# CI and stage publication

[← README](../../README.md) · [All documentation](../../README.md#documentation)

What the GitHub Actions workflow checks and how a completed stage is published.

The `CI` workflow runs for pushes to `main`, pull requests targeting `main` and
manual dispatch. Two jobs run on `ubuntu-24.04`:

```text
checks (10 min): checkout + uv 0.12.23 + Python 3.12
  uv sync --locked → Ruff → Ruff format --check → strict mypy → pytest
  → check_docs.py → python -m app.evaluate
                                  ↓ needs
container (15 min): docker build → non-root user and no dev tools
  → run with fresh token digests → scripts/api_smoke.py
```

No database service, LLM key or production secret is required; tests use a
fake Docker daemon and a fake Prometheus inside the test process. The container
job uses the runner's Docker only to build and run this project's image with
random tokens created for that run.
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

Stage 08 delivers a container image built from the [Dockerfile](../../Dockerfile);
see [container delivery](container.md). CI builds and smoke-tests it, but no
image is pushed to a registry and nothing is deployed. Publishing the API with
operational data needs tokens, TLS in front of the service and a decision about
who may read reports. Delivery of this project's code is separate from the
agent's read-only access to systems under investigation.

**[Detailed CI/CD guide in Russian](../CI_CD.md)** explains the terms, triggers,
step order, permissions, local reproduction and troubleshooting. Actual results
belong in [VERIFICATION.md](../VERIFICATION.md).
