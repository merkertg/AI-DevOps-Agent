"""Check the investigation API: token scopes, idempotent saves, reads and errors."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.application.reporting import render_report
from app.config import Settings
from app.infrastructure.fixtures import load_fixture
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/stage05/after_deployment.json"
READ, WRITE = "fictional-read-token-0000", "fictional-write-token-0000"
UNKNOWN = "inv-" + "0" * 24


def digest(token: str) -> str:
    """Hash a token the way the settings expect it."""
    return hashlib.sha256(token.encode()).hexdigest()


def auth(token: str) -> dict[str, str]:
    """Build a bearer authorization header."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """Return a store path that does not exist yet."""
    return tmp_path / "data" / "investigations.sqlite3"


@pytest.fixture
def api(store: Path) -> Iterator[TestClient]:
    """Run the app with read and write token digests and a temporary store."""
    settings = Settings(
        app_read_token_sha256=digest(READ),
        app_write_token_sha256=digest(WRITE),
        app_store_path=store,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def save(api: TestClient, body: bytes | None = None) -> dict[str, object]:
    """Save the Stage 05 fixture (or a given body) with the write token."""
    response = api.post(
        "/investigations",
        content=FIXTURE.read_bytes() if body is None else body,
        headers={**auth(WRITE), "Content-Type": "application/json"},
    )
    assert response.status_code in {200, 201}, response.text
    return dict(response.json())


def test_investigation_api_is_closed_without_configured_tokens(tmp_path: Path) -> None:
    """With no digests set, every investigation route refuses; health still works."""
    settings = Settings(app_store_path=tmp_path / "store.sqlite3")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        for method, path in (
            ("GET", "/investigations"),
            ("POST", "/investigations"),
            ("GET", f"/investigations/{UNKNOWN}"),
            ("POST", f"/investigations/{UNKNOWN}/agent"),
        ):
            response = client.request(method, path, headers=auth(WRITE))
            assert response.status_code == 403
            assert "disabled until access tokens are configured" in response.text
    assert not (tmp_path / "store.sqlite3").exists()


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Basic Zm9vOmJhcg=="}, 401),
        (auth("fictional-wrong-token"), 401),
        (auth(READ), 200),
        (auth(WRITE), 200),
    ],
)
def test_reading_requires_a_valid_token(
    api: TestClient, headers: dict[str, str], status: int
) -> None:
    """Read routes accept read and write tokens and challenge everything else."""
    response = api.get("/investigations", headers=headers)
    assert response.status_code == status
    if status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert "fictional-wrong-token" not in response.text


def test_saving_requires_the_write_scope(api: TestClient, store: Path) -> None:
    """A read token cannot save, and nothing is written."""
    response = api.post(
        "/investigations",
        content=FIXTURE.read_bytes(),
        headers={**auth(READ), "Content-Type": "application/json"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "The token does not allow this operation."}
    assert not store.exists()


def test_save_is_idempotent_and_reads_back_the_exact_report(
    api: TestClient, store: Path
) -> None:
    """The same body gives one record; reading returns the stored bytes."""
    first = api.post(
        "/investigations",
        content=FIXTURE.read_bytes(),
        headers={**auth(WRITE), "Content-Type": "application/json"},
    )
    assert first.status_code == 201
    receipt = first.json()
    identifier = receipt["investigation_id"]
    assert first.headers["Location"] == f"/investigations/{identifier}"
    assert receipt["created"] is True and receipt["app_version"] == "0.8.0"
    again = save(api)
    assert again["created"] is False and again["stored_at"] == receipt["stored_at"]
    report = api.get(f"/investigations/{identifier}", headers=auth(READ))
    assert report.status_code == 200
    assert report.text == render_report(load_fixture(FIXTURE))
    assert (
        api.get(f"/investigations/{identifier}", headers=auth(READ)).text == report.text
    )
    listing = api.get("/investigations?limit=5", headers=auth(READ)).json()
    assert listing["total"] == 1
    assert listing["investigations"][0]["investigation_id"] == identifier
    assert store.stat().st_mode & 0o077 == 0


def test_verification_and_agent_run_on_a_saved_investigation(api: TestClient) -> None:
    """Verification passes and the offline agent answers without executing anything."""
    identifier = save(api)["investigation_id"]
    verification = api.get(
        f"/investigations/{identifier}/verification", headers=auth(READ)
    )
    assert verification.json()["status"] == "verified"
    agent = api.post(f"/investigations/{identifier}/agent", headers=auth(READ))
    assert agent.status_code == 200
    body = agent.json()
    assert body["agent"]["status"] == "answered"
    assert body["subject"]["investigation_id"] == identifier
    assert body["next_check"]["executed"] is False


@pytest.mark.parametrize(
    ("body", "content_type", "status"),
    [
        (b"{}", "text/plain", 415),
        (b"not json fictional-private-marker", "application/json", 400),
        (b'{"schema_version": 1, "repository": "/etc"}', "application/json", 400),
        (b"x" * 1_048_577, "application/json", 413),
    ],
)
def test_bad_bodies_get_static_errors(
    api: TestClient, store: Path, body: bytes, content_type: str, status: int
) -> None:
    """Wrong types, invalid fixtures and oversize bodies are refused safely."""
    response = api.post(
        "/investigations",
        content=body,
        headers={**auth(WRITE), "Content-Type": content_type},
    )
    assert response.status_code == status
    assert "fictional-private-marker" not in response.text
    assert set(response.json()) == {"detail"}
    assert not store.exists()


def test_streamed_body_over_the_limit_is_refused(api: TestClient) -> None:
    """A body without Content-Length is still counted while it is read."""

    def chunks() -> Iterator[bytes]:
        """Yield more than the limit in pieces."""
        for _ in range(17):
            yield b"x" * 65_536

    response = api.post(
        "/investigations",
        content=chunks(),
        headers={**auth(WRITE), "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_secrets_in_fixture_text_are_redacted_before_storage(
    api: TestClient, store: Path
) -> None:
    """Sanitizing happens before the report or the store sees the text."""
    data = json.loads(FIXTURE.read_text())
    data["observations"][0]["message"] = "Fictional token=fictional-api-secret-0000"
    identifier = save(api, json.dumps(data).encode())["investigation_id"]
    report = api.get(f"/investigations/{identifier}", headers=auth(READ)).text
    assert "fictional-api-secret-0000" not in report
    assert b"fictional-api-secret-0000" not in store.read_bytes()


@pytest.mark.parametrize(
    "path", [UNKNOWN, "fictional-private-marker", "inv-ZZZ", "../etc/passwd"]
)
def test_unknown_or_malformed_ids_are_404(api: TestClient, path: str) -> None:
    """Missing records and bad identifiers are not distinguished or echoed."""
    save(api)
    for suffix in ("", "/verification"):
        response = api.get(f"/investigations/{path}{suffix}", headers=auth(READ))
        assert response.status_code == 404
        assert "fictional-private-marker" not in response.text


def test_reads_before_any_save_do_not_create_the_store(
    api: TestClient, store: Path
) -> None:
    """An empty service lists nothing and creates no file."""
    assert api.get("/investigations", headers=auth(READ)).json() == {
        "total": 0,
        "investigations": [],
    }
    assert api.get(f"/investigations/{UNKNOWN}", headers=auth(READ)).status_code == 404
    assert not store.exists()


def test_tampered_record_is_not_served(api: TestClient, store: Path) -> None:
    """A changed report fails integrity: it is not shown and the agent refuses."""
    identifier = save(api)["investigation_id"]
    with sqlite3.connect(store) as connection:
        connection.execute("DROP TRIGGER investigations_no_update")
        connection.execute(
            "UPDATE investigations SET report_document = report_document || ' '"
        )
        connection.execute(
            "CREATE TRIGGER investigations_no_update BEFORE UPDATE ON investigations "
            "BEGIN SELECT RAISE(ABORT, 'investigations are append-only'); END"
        )
    assert (
        api.get(f"/investigations/{identifier}", headers=auth(READ)).status_code == 409
    )
    agent = api.post(f"/investigations/{identifier}/agent", headers=auth(READ))
    assert agent.status_code == 409
    verification = api.get(
        f"/investigations/{identifier}/verification", headers=auth(READ)
    ).json()
    assert verification["status"] == "failed"


def test_unreadable_store_is_503(api: TestClient, store: Path) -> None:
    """A file that is not a store gives a static unavailable error."""
    store.parent.mkdir(parents=True)
    store.write_bytes(b"fictional-private-marker" * 50)
    response = api.get("/investigations", headers=auth(READ))
    assert response.status_code == 503
    assert "fictional-private-marker" not in response.text


@pytest.mark.parametrize("value", ["abc", "A" * 64, "0" * 63, "fictional-token-0000"])
def test_token_settings_must_be_sha256_digests(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plain tokens or malformed digests are rejected without being echoed."""
    monkeypatch.setenv("APP_WRITE_TOKEN_SHA256", value)
    with pytest.raises(ValidationError) as error:
        Settings()
    assert value not in str(error.value)


def test_openapi_documents_bearer_security(api: TestClient) -> None:
    """The schema shows which routes need a bearer token."""
    schema = api.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/investigations"]["post"]["security"] == [
        {"HTTPBearer": []}
    ]
    assert "security" not in schema["paths"]["/health"]["get"]
