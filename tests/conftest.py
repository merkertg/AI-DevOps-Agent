"""Keep foundation tests independent of local configuration and external services."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

from docker_fake import FakeDocker


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
