"""Compose the Stage 01 HTTP service without accessing external infrastructure."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from app import __version__
from app.config import Settings, get_settings


class HealthResponse(BaseModel):
    """Describe liveness only; this response makes no collector readiness claim."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ok"] = "ok"
    service: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an independent application, allowing explicit settings in tests."""
    resolved_settings = settings if settings is not None else get_settings()
    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        debug=resolved_settings.app_debug,
        description=(
            "Staged learning project for evidence-based incident investigation. "
            "Stage 01 provides service liveness only."
        ),
    )
    application.state.settings = resolved_settings

    @application.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        """Confirm that this HTTP application responds without probing dependencies."""
        return HealthResponse(service=resolved_settings.app_name)

    return application


app = create_app()
