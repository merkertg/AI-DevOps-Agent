"""Exercise the public HTTP contract and application factory without external I/O."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import __version__
from app.config import Settings, get_settings


def test_health_returns_service_status(client: TestClient) -> None:
    """The liveness endpoint returns exactly its documented JSON contract."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok", "service": "ai-devops-agent"}


def test_health_rejects_post(client: TestClient) -> None:
    """An unsupported method cannot be mistaken for a successful health request."""
    assert client.post("/health").status_code == 405


def test_unknown_route_returns_404(client: TestClient) -> None:
    """A future incident API is not exposed by the foundation stage."""
    assert client.get("/incidents").status_code == 404


def test_openapi_describes_health_contract(client: TestClient) -> None:
    """Generated API documentation exposes the version and typed health response."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "ai-devops-agent"
    assert schema["info"]["version"] == __version__ == "0.1.0"
    assert set(schema["paths"]) == {"/health"}
    response_schema = schema["paths"]["/health"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/HealthResponse"}
    health_schema = schema["components"]["schemas"]["HealthResponse"]
    assert health_schema["required"] == ["service"]
    assert health_schema["additionalProperties"] is False
    assert set(health_schema["properties"]) == {"status", "service"}
    assert health_schema["properties"]["status"]["const"] == "ok"
    assert health_schema["properties"]["status"]["default"] == "ok"
    assert health_schema["properties"]["service"]["type"] == "string"


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_api_documentation_is_available(client: TestClient, path: str) -> None:
    """Both documented HTML entry points are available from the local API."""
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_factory_uses_cached_settings_when_none_are_provided() -> None:
    """The normal server entry point uses the same validated process snapshot."""
    from app.main import create_app

    settings = get_settings()
    application = create_app()
    assert application.state.settings is settings
    assert application.title == settings.app_name
    assert application.debug is False


def test_factory_uses_injected_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit configuration determines application metadata and HTTP behavior."""
    from app.main import create_app

    monkeypatch.setenv("APP_NAME", "from-environment")
    settings = Settings(app_name="  configured-agent  ", app_debug=True)
    application = create_app(settings)
    assert application.state.settings is settings
    assert application.title == "configured-agent"
    assert application.debug is True
    with TestClient(application) as injected_client:
        assert injected_client.get("/health").json() == {
            "status": "ok",
            "service": "configured-agent",
        }


def test_app_instances_keep_their_settings_separate() -> None:
    """Creating another application never changes a prior instance's health reply."""
    from app.main import create_app

    first = create_app(Settings(app_name="first"))
    second = create_app(Settings(app_name="second"))
    with TestClient(first) as first_client, TestClient(second) as second_client:
        assert first_client.get("/health").json()["service"] == "first"
        assert second_client.get("/health").json()["service"] == "second"
        assert first_client.get("/health").json()["service"] == "first"


def test_health_response_has_default_success_status() -> None:
    """The typed response supplies its fixed success status when omitted."""
    from app.main import HealthResponse

    assert HealthResponse(service="demo").model_dump() == {
        "status": "ok",
        "service": "demo",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"service": 123},
        {"service": "demo", "status": "failed"},
        {"service": "demo", "internal_details": "hidden"},
    ],
)
def test_health_response_rejects_invalid_payloads(payload: dict[str, object]) -> None:
    """The response model rejects missing, coerced, unexpected and invalid fields."""
    from app.main import HealthResponse

    with pytest.raises(ValidationError):
        HealthResponse.model_validate(payload)
