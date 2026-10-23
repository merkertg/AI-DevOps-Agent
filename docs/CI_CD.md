# Continuous integration and stage publication

[README](../README.md) · [Testing](instructions/testing.md) · [Verification](VERIFICATION.md)

## Stage 01 configuration

`.github/workflows/ci.yml` defines checks for pushes to `main`, pull requests
targeting `main` and manual dispatch. The workflow uses Python 3.12 and uv,
installs the locked project environment and runs the checks below. The workflow
file is configuration; a successful remote run must be established separately.

| Step | Command |
| --- | --- |
| Install | `uv sync --locked` |
| Lint | `uv run --locked ruff check .` |
| Formatting | `uv run --locked ruff format --check .` |
| Types | `uv run --locked mypy app tests scripts` |
| Tests | `uv run --locked pytest` |
| Documentation | `uv run --locked python scripts/check_docs.py` |

No database service, Docker daemon, LLM key or production secret is required.
The uv integration follows the official
[GitHub Actions guide](https://docs.astral.sh/uv/guides/integration/github/):
install uv, select Python and synchronize the environment before running tools.
The actual pinned action revisions and uv version are in the workflow.

## What a stage release requires

Each completed stage is committed and pushed, as requested for this project:

1. Finish the current stage and run the relevant local checks.
2. Update its walkthrough, affected instructions, development log and verification.
3. Review the diff, including ignored local files and accidental sensitive data.
4. Create a focused English commit and push it to the configured remote branch.
5. Confirm the remote branch points to that commit and inspect its GitHub Actions
   run. Record the commit and remote result in the delivery report.

`git commit` alone does not start a remote workflow. A successful push confirms
publication of Git history; it does not establish a passing CI run. A run that is
queued or executing remains pending. If remote results cannot be accessed,
record `not_verified`. Implementation, local checks, push and remote CI each
have their own status. If a push or a check fails, record the actual failure
and repair it. Do not force-push to bypass a remote conflict.

Local results belong in [VERIFICATION.md](VERIFICATION.md) and
[verification.json](guide/verification.json). Remote evidence should identify
the actual commit and run rather than reusing an older successful result.
The [development log](DEVELOPMENT_LOG.md) records what the stage changed.

## Delivery boundaries

Stage 01 implements continuous integration configuration. Container building,
image publication and deployment are not configured here. Stage 08 will add
container checks and a documented delivery path after the investigation workflow
exists. Delivery of this project's code is separate from the agent's read-only
access to systems under investigation.
