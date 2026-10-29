"""Read real fictional Git repositories: scope, hardening, gaps and no changes."""

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import git
import pytest

from app.domain import Incident, ObservationKind
from app.infrastructure.deployments import (
    STAGE05_REPOSITORY,
    HistoryError,
    HistoryLimits,
    collect_deployments,
)
from app.infrastructure.git_history import GitDeploymentReader

from git_lab import clone, create_lab, record, record_deployment, tree_state
from git_lab import git as run_git

START = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
INCIDENT = Incident(
    "after-deployment", "training-api", START, START + timedelta(minutes=30)
)
DEPLOY = "training-api-lab-deployments-0002"


def read(repository: Path, limits: HistoryLimits | None = None) -> Any:
    """Collect deployment evidence for the Stage 05 window from one repository."""
    if limits is None:
        return collect_deployments(GitDeploymentReader(repository), INCIDENT)
    return collect_deployments(GitDeploymentReader(repository), INCIDENT, limits=limits)


def deployments(repository: Path) -> list[str]:
    """Return the IDs of deployment observations read from a repository."""
    return [
        item.observation_id
        for item in read(repository).observations
        if item.kind is ObservationKind.DEPLOYMENT
    ]


def test_lab_tool_builds_the_allowlisted_identity_reproducibly(
    lab_template: Path, tmp_path: Path
) -> None:
    """The root commit pinned in code is what the lab tool builds every time."""
    rebuilt = tmp_path / "again"
    assert create_lab(rebuilt) == STAGE05_REPOSITORY.root_commit
    for repository in (lab_template, rebuilt):
        assert run_git(repository, "rev-list", "--max-parents=0", "HEAD") == (
            STAGE05_REPOSITORY.root_commit
        )
    listing = ("for-each-ref", "--format=%(refname) %(objectname)")
    assert run_git(lab_template, *listing) == run_git(rebuilt, *listing)


def test_lab_deployment_inside_the_window_is_read(lab_repository: Path) -> None:
    """Only deployment 0002 falls inside the window; 0001 is older."""
    evidence = read(lab_repository)
    deploy, summary = evidence.observations
    head = run_git(lab_repository, "rev-parse", "HEAD")
    tag_object = run_git(lab_repository, "rev-parse", "deploy/lab/0002")
    assert deploy.observation_id == DEPLOY
    assert deploy.event_time == START + timedelta(minutes=20)
    assert f"revision={head}" in deploy.message
    assert f"tag object {tag_object}" in deploy.message
    assert "Commit summary: Change the dependency port to 5432" in deploy.message
    assert "examined 2 annotated tags" in summary.message
    assert evidence.gaps == ()


def test_reading_history_does_not_change_the_repository(lab_repository: Path) -> None:
    """Every file under the working tree and .git keeps its bytes, mode and mtime."""
    before = tree_state(lab_repository)
    read(lab_repository)
    read(lab_repository)
    assert tree_state(lab_repository) == before


def test_only_read_only_object_queries_run(
    lab_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitPython reads refs itself and runs git only as cat-file."""
    commands: list[list[str]] = []
    original: Callable[..., Any] = git.cmd.Git.execute

    def recording(self: git.cmd.Git, command: Any, *args: Any, **kwargs: Any) -> Any:
        """Record each git command line before running it."""
        commands.append([str(part) for part in command])
        return original(self, command, *args, **kwargs)

    monkeypatch.setattr(git.cmd.Git, "execute", recording)
    read(lab_repository)
    assert commands
    assert {command[1] for command in commands} == {"cat-file"}
    assert {command[2] for command in commands} <= {"-e", "--batch-check", "--batch"}


def test_hostile_repository_configuration_and_hooks_never_run(
    lab_repository: Path,
) -> None:
    """Config commands and hooks that git could execute stay inert."""
    marker = lab_repository.parent / "fictional-executed-marker"
    hooks = lab_repository / ".git" / "hooks"
    hooks.mkdir()
    for hook in (
        "post-checkout",
        "pre-commit",
        "reference-transaction",
        "post-index-change",
    ):
        script = hooks / hook
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        script.chmod(0o755)
    for key, value in (
        ("core.fsmonitor", f"touch '{marker}'"),
        ("core.hooksPath", str(hooks)),
        ("core.pager", f"touch '{marker}'"),
        ("alias.cat-file", f"!touch '{marker}'"),
    ):
        run_git(lab_repository, "config", "--local", key, value)
    assert deployments(lab_repository) == [DEPLOY]
    assert not marker.exists()


def test_replacement_objects_are_ignored(lab_repository: Path) -> None:
    """refs/replace cannot substitute a different commit for a deployed revision."""
    head = run_git(lab_repository, "rev-parse", "HEAD")
    tree = run_git(lab_repository, "rev-parse", "HEAD^{tree}")
    forged = run_git(
        lab_repository, "commit-tree", tree, "-m", "Fictional replaced summary"
    )
    run_git(lab_repository, "replace", head, forged)
    message = read(lab_repository).observations[0].message
    assert "Change the dependency port to 5432" in message
    assert "replaced" not in message


def test_new_deployment_is_seen_by_a_new_read(lab_repository: Path) -> None:
    """A deployment recorded after a first read appears in the next read."""
    assert deployments(lab_repository) == [DEPLOY]
    record_deployment(lab_repository, START + timedelta(minutes=26))
    assert deployments(lab_repository) == [
        DEPLOY,
        "training-api-lab-deployments-0003",
    ]


def test_packed_refs_are_read_like_loose_refs(lab_repository: Path) -> None:
    """Packing refs does not hide deployment tags."""
    run_git(lab_repository, "pack-refs", "--all")
    assert not (lab_repository / ".git/refs/tags/deploy/lab/0002").exists()
    assert deployments(lab_repository) == [DEPLOY]


def test_tag_problems_become_gaps_and_other_namespaces_are_ignored(
    lab_repository: Path,
) -> None:
    """Read problems come first, then record problems; other prefixes are unread."""
    deployed = record(START + timedelta(minutes=21))
    first_tag = run_git(lab_repository, "rev-parse", "deploy/lab/0001")
    run_git(lab_repository, "tag", "deploy/lab/0003")
    run_git(
        lab_repository, "tag", "-a", "deploy/lab/0004", "-m", deployed, "HEAD^{tree}"
    )
    run_git(lab_repository, "tag", "-a", "deploy/lab/0005", "-m", "not a record")
    run_git(lab_repository, "tag", "-a", "deploy/lab/0006", "-m", "x" * 5_000)
    run_git(lab_repository, "update-ref", "refs/tags/deploy/lab/0007", first_tag)
    run_git(lab_repository, "tag", "-a", "deploy/lab/latest", "-m", deployed)
    run_git(lab_repository, "tag", "-a", "deploy/prod/0001", "-m", deployed)
    run_git(lab_repository, "tag", "-a", "deploy/lab2/0001", "-m", deployed)
    evidence = read(lab_repository)
    assert [item.observation_id for item in evidence.observations] == [
        DEPLOY,
        "training-api-lab-deployments-summary",
    ]
    assert evidence.gaps == (
        "Deployment tag 0003 is not an annotated tag; it was skipped.",
        "Deployment tag 0004 does not point to a commit; it was skipped.",
        "Deployment tag 0006 exceeds 4096 bytes; it was skipped.",
        "Deployment tag 0007 does not match the name in its tag object; "
        "it was skipped.",
        "Deployment tag 0005 has no valid deployment record; it was skipped.",
        "A deployment tag with an unsupported name was skipped.",
    )


def test_tag_count_is_bounded(lab_repository: Path) -> None:
    """Tags after the limit are not read, and the omission is a gap."""
    evidence = read(lab_repository, HistoryLimits(max_records=1))
    assert "examined 1 annotated tags" in evidence.observations[-1].message
    assert evidence.gaps == (
        "More than 1 deployment tags exist; tags after the first 1 by name "
        "were not examined.",
    )


def test_unreadable_tag_object_is_a_per_tag_gap(lab_repository: Path) -> None:
    """A missing tag object skips that tag without hiding the others."""
    tag_object = run_git(lab_repository, "rev-parse", "deploy/lab/0002")
    loose = lab_repository / ".git/objects" / tag_object[:2] / tag_object[2:]
    loose.unlink()
    evidence = read(lab_repository)
    assert evidence.gaps == ("Deployment tag 0002 could not be read; it was skipped.",)


@pytest.mark.parametrize("contents", ["not-an-id\n", "", "ref:\n"])
def test_corrupt_ref_does_not_stop_the_read(
    lab_repository: Path, contents: str
) -> None:
    """A ref file without an object ID is skipped while valid tags are read."""
    (lab_repository / ".git/refs/tags/deploy/lab/0009").write_text(contents)
    evidence = read(lab_repository)
    assert [item.observation_id for item in evidence.observations][0] == DEPLOY
    assert evidence.gaps == ("Deployment tag 0009 could not be read; it was skipped.",)


def test_malformed_tag_headers_are_a_safe_gap(lab_repository: Path) -> None:
    """A truncated raw tag cannot escape as a parser traceback or hide valid tags."""
    malformed = lab_repository / "broken-tag.txt"
    revision = run_git(lab_repository, "rev-parse", "HEAD")
    malformed.write_text(f"object {revision}\ntype commit\n")
    object_id = run_git(
        lab_repository, "hash-object", "-t", "tag", "-w", "--literally", str(malformed)
    )
    # Git refuses to create a ref to a malformed object; model corrupt disk data.
    (lab_repository / ".git/refs/tags/deploy/lab/0009").write_text(object_id + "\n")
    evidence = read(lab_repository)
    assert evidence.observations[0].observation_id == DEPLOY
    assert evidence.gaps == ("Deployment tag 0009 could not be read; it was skipped.",)


@pytest.mark.parametrize(
    "entry", ["HEAD", "config", "packed-refs", "objects", "refs/tags/deploy/lab"]
)
def test_internal_git_redirects_are_a_safe_gap(
    entry: str, lab_repository: Path, tmp_path: Path
) -> None:
    """Git data directories and metadata cannot redirect a read outside the lab."""
    target = lab_repository / ".git" / entry
    outside = tmp_path / "fictional-outside-git-data"
    if target.exists():
        target.rename(outside)
    else:
        outside.write_text("# pack-refs with: peeled fully-peeled sorted\n")
    target.symlink_to(outside, target_is_directory=outside.is_dir())
    evidence = read(lab_repository)
    assert evidence.observations == ()
    assert len(evidence.gaps) == 1
    assert "could not be read" in evidence.gaps[0]
    assert str(outside) not in evidence.gaps[0]


def test_loose_tag_object_link_is_not_followed(
    lab_repository: Path, tmp_path: Path
) -> None:
    """A tag's loose object path is checked before Git reads its bytes."""
    object_id = run_git(lab_repository, "rev-parse", "deploy/lab/0002")
    target = lab_repository / ".git/objects" / object_id[:2] / object_id[2:]
    outside = tmp_path / "fictional-outside-object"
    target.rename(outside)
    target.symlink_to(outside)
    evidence = read(lab_repository)
    assert evidence.gaps == ("Deployment tag 0002 could not be read; it was skipped.",)
    assert all(
        event.kind is not ObservationKind.DEPLOYMENT for event in evidence.observations
    )


def test_loose_commit_object_link_is_not_followed(
    lab_repository: Path, tmp_path: Path
) -> None:
    """The commit object named by an allowed tag cannot redirect to another file."""
    object_id = run_git(lab_repository, "rev-parse", "HEAD")
    target = lab_repository / ".git/objects" / object_id[:2] / object_id[2:]
    outside = tmp_path / "fictional-outside-commit"
    target.rename(outside)
    target.symlink_to(outside)
    assert read(lab_repository).gaps == (
        "Deployment tag 0002 could not be read; it was skipped.",
    )


def test_loose_tag_ref_link_is_not_followed(
    lab_repository: Path, tmp_path: Path
) -> None:
    """A loose ref file is checked before resolving its otherwise valid object ID."""
    target = lab_repository / ".git/refs/tags/deploy/lab/0002"
    outside = tmp_path / "fictional-outside-ref"
    target.rename(outside)
    target.symlink_to(outside)
    assert read(lab_repository).gaps == (
        "Deployment tag 0002 could not be read; it was skipped.",
    )


def test_pack_file_link_is_a_safe_gap(lab_repository: Path, tmp_path: Path) -> None:
    """A pack directory containing linked data is refused before object queries."""
    outside = tmp_path / "fictional-outside-pack"
    outside.write_bytes(b"fictional pack data")
    (lab_repository / ".git/objects/pack/linked.pack").symlink_to(outside)
    evidence = read(lab_repository)
    assert evidence.observations == ()
    assert "could not be read" in evidence.gaps[0]


@pytest.mark.parametrize("kind", ["missing", "plain-directory", "file"])
def test_missing_or_non_repository_paths_are_gaps(kind: str, tmp_path: Path) -> None:
    """Unavailable history is reported, never treated as having no deployments."""
    path = tmp_path / "fictional-private-dir"
    if kind == "plain-directory":
        path.mkdir()
    elif kind == "file":
        path.write_text("fictional")
    evidence = read(path)
    assert evidence.observations == ()
    assert len(evidence.gaps) == 1
    assert "was not found or is not a Git working repository" in evidence.gaps[0]
    assert "fictional-private-dir" not in evidence.gaps[0]


def test_symbolic_links_and_gitfiles_are_gaps(
    lab_repository: Path, tmp_path: Path
) -> None:
    """The repository and its .git must be real directories, not redirects."""
    link = tmp_path / "link"
    link.symlink_to(lab_repository)
    pointer = tmp_path / "pointer"
    pointer.mkdir()
    (pointer / ".git").write_text(f"gitdir: {lab_repository / '.git'}\n")
    linked_git = tmp_path / "linked-git"
    linked_git.mkdir()
    (linked_git / ".git").symlink_to(lab_repository / ".git")
    for path in (link, pointer, linked_git):
        assert "not found or is not" in read(path).gaps[0]


@pytest.mark.parametrize("entry", ["objects/info/alternates", "reftable", "commondir"])
def test_redirecting_repository_layouts_are_unsupported(
    entry: str, lab_repository: Path
) -> None:
    """Alternates, reftable and linked worktrees are refused as a gap."""
    target = lab_repository / ".git" / entry
    if entry == "reftable":
        target.mkdir()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("/elsewhere\n")
    evidence = read(lab_repository)
    assert evidence.observations == ()
    assert "not supported" in evidence.gaps[0]


def test_other_repository_is_refused(tmp_path: Path) -> None:
    """A repository without the allowlisted root commit is not read."""
    other = tmp_path / "other"
    other.mkdir()
    run_git(other, "init", "--quiet")
    (other / "file.txt").write_text("fictional\n")
    run_git(other, "add", "file.txt")
    run_git(other, "commit", "--quiet", "-m", "Fictional other project")
    run_git(other, "tag", "-a", "deploy/lab/0001", "-m", record(START))
    with pytest.raises(HistoryError, match="not the allowlisted lab repository"):
        read(other)


def test_repository_that_only_contains_a_copy_of_the_root_is_accepted(
    lab_repository: Path, tmp_path: Path
) -> None:
    """A clone shares the root commit, so it is the same project's history."""
    copy = tmp_path / "clone"
    clone(lab_repository, copy)
    assert deployments(copy) == [DEPLOY]


@pytest.mark.parametrize("name", ["GIT_DIR", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR"])
def test_redirecting_environment_is_refused(
    name: str, lab_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Variables that would point Git at other data stop the read."""
    monkeypatch.setenv(name, str(lab_repository / ".git"))
    with pytest.raises(HistoryError, match="redirect"):
        read(lab_repository)


def test_injected_git_configuration_is_ignored(
    lab_repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User, system and environment configuration do not reach the reader."""
    marker = tmp_path / "fictional-executed-marker"
    config = tmp_path / "gitconfig"
    config.write_text(f"[core]\n\tfsmonitor = touch '{marker}'\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.useReplaceRefs")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    head = run_git(lab_repository, "rev-parse", "HEAD")
    tree = run_git(lab_repository, "rev-parse", "HEAD^{tree}")
    forged = run_git(lab_repository, "commit-tree", tree, "-m", "Fictional replaced")
    run_git(lab_repository, "replace", head, forged)
    assert "Fictional replaced" not in read(lab_repository).observations[0].message
    assert not marker.exists()
    assert os.environ["GIT_CONFIG_GLOBAL"] == str(config)
