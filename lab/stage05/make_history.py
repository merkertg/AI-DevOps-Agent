"""Build or extend the fictional Stage 05 lab repository with deployment tags.

This is a lab setup tool, like `docker compose up`: it writes only to a new
repository it creates, or adds a deployment tag to that lab repository. The
investigation code never runs it and never writes to a repository.
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

AUTHOR = ("Fictional Developer", "developer@example.invalid")
TAG_PREFIX = "deploy/lab/"
RECORD_DELAY = timedelta(seconds=30)
FIRST_DEPLOYMENT_AT = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
LATEST_DEPLOYMENT_AT = datetime(2026, 10, 29, 10, 20, tzinfo=UTC)
# Fixed contents and times make every commit ID, including the root, reproducible.
COMMITS = (
    (
        datetime(2026, 10, 20, 9, 0, tzinfo=UTC),
        "Add fictional training API",
        {
            "README.md": "# Fictional training API\n\nUsed by the Stage 05 lab.\n",
            "settings.ini": "[dependency]\nhost = 127.0.0.1\nport = 15432\n",
        },
    ),
    (
        datetime(2026, 10, 27, 14, 30, tzinfo=UTC),
        "Read the dependency endpoint from settings",
        {"service.py": '"""Fictional service."""\n\nSETTINGS = "settings.ini"\n'},
    ),
    (
        datetime(2026, 10, 29, 9, 40, tzinfo=UTC),
        "Change the dependency port to 5432",
        {"settings.ini": "[dependency]\nhost = 127.0.0.1\nport = 5432\n"},
    ),
)


def _environment(at: datetime) -> dict[str, str]:
    """Isolate git from personal configuration and fix the identity and time."""
    stamp = at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")
    name, email = AUTHOR
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    }


def git(repository: Path, *arguments: str, at: datetime) -> str:
    """Run git on exactly this repository; a parent repository is never found."""
    result = subprocess.run(
        [
            "git",
            f"--git-dir={repository / '.git'}",
            f"--work-tree={repository}",
            *arguments,
        ],
        env=_environment(at),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


def deployment_record(deployed_at: datetime) -> str:
    """Write the structured tag message that the collector accepts."""
    return json.dumps(
        {
            "record": "ai-devops-agent.deployment",
            "version": 1,
            "service": "training-api",
            "environment": "lab",
            "deployed_at": deployed_at.astimezone(UTC).isoformat(),
        },
        sort_keys=True,
    )


def tag_deployment(
    repository: Path, sequence: int, revision: str, deployed_at: datetime
) -> None:
    """Record one deployment as an annotated tag written shortly after it."""
    git(
        repository,
        "tag",
        "-a",
        f"{TAG_PREFIX}{sequence:04d}",
        "-m",
        deployment_record(deployed_at),
        revision,
        at=deployed_at + RECORD_DELAY,
    )


def create_history(
    destination: Path, latest_deployment_at: datetime = LATEST_DEPLOYMENT_AT
) -> str:
    """Create the lab repository at a new path and return its root commit ID."""
    if latest_deployment_at.utcoffset() is None:
        raise ValueError("The deployment time must include a timezone.")
    if latest_deployment_at < COMMITS[-1][0]:
        raise ValueError("The latest deployment must not precede its commit.")
    destination.mkdir(parents=True, exist_ok=False)
    subprocess.run(
        ["git", "init", "--quiet", "--template=", "--initial-branch=main", "--"]
        + [str(destination)],
        env=_environment(COMMITS[0][0]),
        capture_output=True,
        check=True,
        timeout=30,
    )
    revisions: list[str] = []
    for at, message, files in COMMITS:
        for name, content in files.items():
            (destination / name).write_bytes(content.encode("utf-8"))
        git(destination, "add", "--", *sorted(files), at=at)
        git(destination, "commit", "--quiet", "--no-verify", "-m", message, at=at)
        revisions.append(git(destination, "rev-parse", "HEAD", at=at))
    tag_deployment(destination, 1, revisions[1], FIRST_DEPLOYMENT_AT)
    tag_deployment(destination, 2, revisions[2], latest_deployment_at)
    return revisions[0]


def record_next_deployment(repository: Path, deployed_at: datetime) -> str:
    """Tag the current HEAD as the next deployment and return the tag name."""
    if deployed_at.utcoffset() is None:
        raise ValueError("The deployment time must include a timezone.")
    existing = git(repository, "tag", "--list", f"{TAG_PREFIX}*", at=deployed_at)
    sequence = len(existing.splitlines()) + 1
    revision = git(repository, "rev-parse", "HEAD", at=deployed_at)
    tag_deployment(repository, sequence, revision, deployed_at)
    return f"{TAG_PREFIX}{sequence:04d}"


def _aware(value: str) -> datetime:
    """Parse an ISO-8601 time with an explicit offset for argparse."""
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("time must include a UTC offset")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    """Create the lab repository or record one more lab deployment."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create the lab repository")
    create.add_argument("destination", type=Path, help="New directory to create")
    create.add_argument(
        "--latest-deployment-at",
        type=_aware,
        default=LATEST_DEPLOYMENT_AT,
        help="Time of the second deployment (default 2026-10-29T10:20:00Z)",
    )
    deploy = commands.add_parser("deploy", help="Tag HEAD as the next deployment")
    deploy.add_argument("repository", type=Path, help="Existing lab repository")
    deploy.add_argument("--at", type=_aware, required=True, help="Deployment time")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "create":
            root = create_history(arguments.destination, arguments.latest_deployment_at)
            print(f"created lab repository with root commit {root}")
        else:
            tag = record_next_deployment(arguments.repository, arguments.at)
            print(f"recorded {tag}")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
