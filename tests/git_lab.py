"""Build fictional Git lab repositories for tests through the Stage 05 lab tool."""

import hashlib
import importlib.util
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
LAB_SCRIPT = ROOT / "lab/stage05/make_history.py"
LATEST_DEPLOYMENT_AT = datetime(2026, 10, 29, 10, 20, tzinfo=UTC)


def _load_lab_tool() -> ModuleType:
    """Import the lab script by path; lab/ is a tool directory, not a package."""
    spec = importlib.util.spec_from_file_location("stage05_make_history", LAB_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LAB = _load_lab_tool()


def create_lab(
    destination: Path, latest_deployment_at: datetime = LATEST_DEPLOYMENT_AT
) -> str:
    """Create the documented lab repository and return its root commit."""
    return str(_LAB.create_history(destination, latest_deployment_at))


def record_deployment(repository: Path, deployed_at: datetime) -> str:
    """Tag HEAD as the next lab deployment and return the tag name."""
    return str(_LAB.record_next_deployment(repository, deployed_at))


def git(repository: Path, *arguments: str, at: datetime = LATEST_DEPLOYMENT_AT) -> str:
    """Run git on one test repository with an isolated, fixed identity and time."""
    return str(_LAB.git(repository, *arguments, at=at))


def clone(source: Path, destination: Path) -> None:
    """Copy a lab repository through git clone without sharing its object files."""
    subprocess.run(
        ["git", "clone", "--quiet", "--no-local", str(source), str(destination)],
        env=_LAB._environment(LATEST_DEPLOYMENT_AT),
        capture_output=True,
        check=True,
        timeout=30,
    )


def record(deployed_at: datetime) -> str:
    """Return the structured tag message the lab tool writes."""
    return str(_LAB.deployment_record(deployed_at))


def tree_state(path: Path) -> dict[str, tuple[int, int, str]]:
    """Capture mode, modification time and content hash of every file and link."""
    state: dict[str, tuple[int, int, str]] = {}
    for directory, folders, files in os.walk(path):
        for name in [*folders, *files]:
            item = Path(directory) / name
            metadata = item.lstat()
            digest = ""
            if item.is_file() and not item.is_symlink():
                digest = hashlib.sha256(item.read_bytes()).hexdigest()
            state[str(item.relative_to(path))] = (
                metadata.st_mode,
                metadata.st_mtime_ns,
                digest,
            )
    return state
