"""Keep foundation tests independent of local configuration and external services."""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

from docker_fake import FakeDocker
from git_lab import create_lab


@pytest.fixture(autouse=True)
def isolated_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """Run each test in an empty directory with a fresh application settings cache."""
    for name in tuple(os.environ):
        if name.upper().startswith("APP_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Send HTTP requests directly to a fresh application without opening a socket."""
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def fake_docker() -> Iterator[FakeDocker]:
    """Provide a fake Docker daemon on a Unix socket; no real Docker is contacted."""
    daemon = FakeDocker()
    try:
        yield daemon
    finally:
        daemon.close()


@pytest.fixture(scope="session")
def lab_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the fictional Stage 05 repository once; tests copy it before use."""
    repository = tmp_path_factory.mktemp("lab-template") / "training-api"
    create_lab(repository)
    return repository


@pytest.fixture
def lab_repository(lab_template: Path, tmp_path: Path) -> Path:
    """Give each test its own copy of the lab repository to read or extend."""
    repository = tmp_path / "training-api"
    shutil.copytree(lab_template, repository, symlinks=True)
    return repository
