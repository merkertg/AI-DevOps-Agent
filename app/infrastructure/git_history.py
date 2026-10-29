"""Read deployment tags of the allowlisted lab repository through GitPython.

Refs are read from files by GitPython; objects are read by `git cat-file`.
No working tree, index, network or write command is ever run.
"""

import os
import stat
from pathlib import Path

from git import Repo
from git.exc import GitCommandError, GitError, ODBError
from git.objects import Commit, TagObject
from git.objects.util import from_timestamp
from git.refs.tag import TagReference

from app.infrastructure.deployments import (
    DeploymentTag,
    HistoryError,
    HistoryLimits,
    HistoryUnavailable,
    RepositoryScope,
    TagListing,
    tag_label,
)

# These variables make Git or GitPython read another repository, index or objects.
_REDIRECTING_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)
# Git subprocesses ignore user, system and injected configuration, replacement
# objects and lazy fetches from promisor remotes.
_GIT_ENVIRONMENT = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_COUNT": "0",
    "GIT_CONFIG_PARAMETERS": "",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}
# Linked worktrees, alternates and reftable can redirect or hide what is read.
_UNSUPPORTED_ENTRIES = ("commondir", "objects/info/alternates", "reftable")
_READ_ERRORS = (
    GitError,
    ODBError,
    OSError,
    ValueError,
    TypeError,
    AttributeError,
    IndexError,
    KeyError,
    OverflowError,
    AssertionError,
)


def _not_found(scope: RepositoryScope) -> HistoryUnavailable:
    """Describe a missing or non-repository path without disclosing it."""
    return HistoryUnavailable(
        f"Deployment history repository {scope.name} was not found or is not a "
        "Git working repository; deployments are unknown."
    )


def _unreadable(scope: RepositoryScope) -> HistoryUnavailable:
    """Describe a repository that exists but could not be read."""
    return HistoryUnavailable(
        f"Deployment history of repository {scope.name} could not be read; "
        "deployments are unknown."
    )


def _plain_components(base: Path, relative: str) -> bool:
    """Reject links and devices along one Git data path without opening them."""
    current = base
    for part in Path(relative).parts:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            return False
    return True


def _plain_object(repo: Repo, object_id: str) -> bool:
    """Check the exact loose-object path before Git can follow a filesystem link."""
    return _plain_components(
        Path(repo.git_dir), f"objects/{object_id[:2]}/{object_id[2:]}"
    )


def _git_dir(path: Path, scope: RepositoryScope) -> str:
    """Require a real working directory with a real .git directory and no redirects."""
    git_dir = path / ".git"
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode) or not stat.S_ISDIR(
            os.lstat(git_dir).st_mode
        ):
            raise _not_found(scope)
    except OSError:
        raise _not_found(scope) from None
    if any(os.path.lexists(git_dir / entry) for entry in _UNSUPPORTED_ENTRIES):
        raise HistoryUnavailable(
            f"Deployment history repository {scope.name} uses linked worktrees, "
            "alternates or reftable, which are not supported; deployments are unknown."
        )
    guarded = (
        "HEAD",
        "config",
        "packed-refs",
        "objects/info",
        "objects/pack",
        f"refs/tags/{scope.tag_prefix.rstrip('/')}",
    )
    if not all(_plain_components(git_dir, entry) for entry in guarded):
        raise _unreadable(scope)
    pack_directory = git_dir / "objects/pack"
    try:
        if pack_directory.is_dir() and any(
            not _plain_components(git_dir, str(entry.relative_to(git_dir)))
            for entry in pack_directory.iterdir()
        ):
            raise _unreadable(scope)
    except OSError:
        raise _unreadable(scope) from None
    return os.path.abspath(git_dir)


def _require_identity(repo: Repo, scope: RepositoryScope) -> None:
    """Accept only a repository whose objects include the allowlisted root commit."""
    if not _plain_object(repo, scope.root_commit):
        raise _unreadable(scope)
    try:
        # Exit status 1 means the object is absent; other failures are read errors.
        repo.git.cat_file("-e", scope.root_commit)
    except GitCommandError as error:
        if error.status == 1:
            raise HistoryError(
                "The repository is not the allowlisted lab repository; it was not read."
            ) from None
        raise _unreadable(scope) from None
    if repo.commit(scope.root_commit).parents:
        raise HistoryError(
            "The repository is not the allowlisted lab repository; it was not read."
        )


def _text(value: str | bytes) -> str:
    """Decode a Git text field without failing on invalid bytes."""
    return value if isinstance(value, str) else value.decode("utf-8", "replace")


def _read_tag(
    reference: TagReference, name: str, label: str, limits: HistoryLimits
) -> DeploymentTag | str:
    """Read one tag within size limits; return a gap when it cannot be a record."""
    if not _plain_components(Path(reference.repo.git_dir), reference.path):
        return f"{label} could not be read; it was skipped."
    # Resolve an object ID only after guarding the ref file itself.
    object_id = TagReference.dereference_recursive(reference.repo, reference.path)
    if not _plain_object(reference.repo, object_id):
        return f"{label} could not be read; it was skipped."
    target = reference.object
    if not isinstance(target, TagObject):
        return f"{label} is not an annotated tag; it was skipped."
    if target.size > limits.max_tag_bytes:
        return f"{label} exceeds {limits.max_tag_bytes} bytes; it was skipped."
    if target.tag != name:
        return f"{label} does not match the name in its tag object; it was skipped."
    commit = target.object
    if not isinstance(commit, Commit):
        return f"{label} does not point to a commit; it was skipped."
    if not _plain_object(reference.repo, commit.hexsha):
        return f"{label} could not be read; it was skipped."
    if commit.size > limits.max_commit_bytes:
        return (
            f"{label} points to a commit larger than {limits.max_commit_bytes} "
            "bytes; it was skipped."
        )
    return DeploymentTag(
        name=name,
        tag_object=target.hexsha,
        message=_text(target.message),
        recorded_at=from_timestamp(target.tagged_date, target.tagger_tz_offset),
        revision=commit.hexsha,
        committed_at=commit.committed_datetime,
        summary=_text(commit.summary),
    )


def _list_tags(repo: Repo, scope: RepositoryScope, limits: HistoryLimits) -> TagListing:
    """Read tags under the allowlisted prefix in name order, up to the limit."""
    prefix = f"refs/tags/{scope.tag_prefix}"
    references: list[TagReference] = []
    gaps: list[str] = []
    for reference in TagReference.iter_items(repo, common_path=prefix.rstrip("/")):
        if not reference.path.startswith(prefix):
            continue
        if len(references) == limits.max_records:
            gaps.append(
                f"More than {limits.max_records} deployment tags exist; tags after "
                f"the first {limits.max_records} by name were not examined."
            )
            break
        references.append(reference)
    tags: list[DeploymentTag] = []
    for reference in references:
        name = reference.path.removeprefix("refs/tags/")
        label = tag_label(name.removeprefix(scope.tag_prefix))
        try:
            result = _read_tag(reference, name, label, limits)
        except _READ_ERRORS:
            result = f"{label} could not be read; it was skipped."
        if isinstance(result, str):
            gaps.append(result)
        else:
            tags.append(result)
    return TagListing(tuple(tags), tuple(gaps))


class GitDeploymentReader:
    """Read deployment tags from one local repository without changing it."""

    def __init__(self, path: Path) -> None:
        """Remember the repository path chosen by the developer."""
        self._path = path

    def read_tags(self, scope: RepositoryScope, limits: HistoryLimits) -> TagListing:
        """Check environment, layout and identity, then read bounded tags."""
        if any(name in os.environ for name in _REDIRECTING_VARIABLES):
            raise HistoryError(
                "Git environment variables that redirect repository access are set; "
                "unset them before reading history."
            )
        git_dir = _git_dir(self._path, scope)
        try:
            repo = Repo(self._path)
        except _READ_ERRORS:
            raise _not_found(scope) from None
        try:
            repo.git.update_environment(GIT_DIR=git_dir, **_GIT_ENVIRONMENT)
            _require_identity(repo, scope)
            return _list_tags(repo, scope, limits)
        except (HistoryError, HistoryUnavailable):
            raise
        except _READ_ERRORS:
            raise _unreadable(scope) from None
        finally:
            repo.close()
