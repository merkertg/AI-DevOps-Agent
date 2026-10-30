"""Expose saved investigations over HTTP behind read and write token scopes.

The API accepts only inline fictional fixture documents. It never reads a
server path, Docker, Git or Prometheus on behalf of a client; those collectors
remain host CLIs. Every error has a static message.
"""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import __version__
from app.agent.contracts import AgentError
from app.agent.graph import run_agent
from app.agent.offline import OfflineRulesProvider
from app.agent.tools import build_view
from app.application.records import (
    INVESTIGATION_ID,
    InvestigationRecord,
    build_record,
    integrity_problems,
    verify_record,
)
from app.application.snapshots import decode_input
from app.cli_support import json_document
from app.config import Settings
from app.infrastructure.fixtures import DEFAULT_LIMITS, FixtureError, parse_fixture
from app.infrastructure.sqlite_store import SqliteInvestigationStore, StoreError

Scope = Literal["read", "write"]
MAX_BODY_BYTES = DEFAULT_LIMITS.max_input_bytes
_bearer = HTTPBearer(auto_error=False, description="Investigation API token")
router = APIRouter(prefix="/investigations", tags=["investigations"])


def _settings(request: Request) -> Settings:
    """Return the settings this application instance was built with."""
    settings: Settings = request.app.state.settings
    return settings


def _digest(token: str) -> str:
    """Hash a presented token for comparison with configured digests."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _matches(digest: str, configured: str | None) -> bool:
    """Compare digests in constant time; an unset digest never matches."""
    return configured is not None and hmac.compare_digest(digest, configured)


def _authorize(
    scope: Scope, request: Request, credentials: HTTPAuthorizationCredentials | None
) -> None:
    """Require a configured API and a bearer token whose scope covers the request."""
    settings = _settings(request)
    if not settings.investigation_api_enabled:
        raise HTTPException(
            403, "The investigation API is disabled until access tokens are configured."
        )
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            401, "A bearer token is required.", {"WWW-Authenticate": "Bearer"}
        )
    digest = _digest(credentials.credentials)
    writes = _matches(digest, settings.app_write_token_sha256)
    reads = writes or _matches(digest, settings.app_read_token_sha256)
    if not reads:
        raise HTTPException(
            401, "The bearer token is not valid.", {"WWW-Authenticate": "Bearer"}
        )
    if scope == "write" and not writes:
        raise HTTPException(403, "The token does not allow this operation.")


def require_read(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Allow read and write tokens."""
    _authorize("read", request, credentials)


def require_write(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Allow only the write token."""
    _authorize("write", request, credentials)


def _json(document: str, status_code: int = 200) -> Response:
    """Return an already rendered JSON document byte for byte."""
    return Response(document, status_code=status_code, media_type="application/json")


def _open_for_reading(settings: Settings) -> SqliteInvestigationStore | None:
    """Open the store read-only, or return None when nothing was saved yet."""
    if not settings.app_store_path.exists():
        return None
    try:
        return SqliteInvestigationStore.open(settings.app_store_path, create=False)
    except StoreError:
        raise HTTPException(503, "The investigation store is unavailable.") from None


def _record(settings: Settings, investigation_id: str) -> InvestigationRecord:
    """Find one record or answer 404 without echoing the identifier."""
    if not INVESTIGATION_ID.fullmatch(investigation_id):
        raise HTTPException(404, "Investigation was not found.")
    store = _open_for_reading(settings)
    if store is None:
        raise HTTPException(404, "Investigation was not found.")
    try:
        with store:
            record = store.get(investigation_id)
    except StoreError:
        raise HTTPException(503, "The investigation store is unavailable.") from None
    if record is None:
        raise HTTPException(404, "Investigation was not found.")
    return record


async def _read_body(request: Request) -> bytes:
    """Read a JSON body of at most MAX_BODY_BYTES, refusing anything larger."""
    media_type = request.headers.get("content-type", "").split(";")[0].strip()
    if media_type != "application/json":
        raise HTTPException(415, "Send the fixture as application/json.")
    declared = request.headers.get("content-length")
    if declared is not None and (
        not declared.isdigit() or int(declared) > MAX_BODY_BYTES
    ):
        raise HTTPException(413, "The fixture exceeds the size limit.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(413, "The fixture exceeds the size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _save(settings: Settings, data: bytes) -> tuple[str, bool, str]:
    """Parse, build and append one record; return its ID, novelty and receipt."""
    try:
        prepared = parse_fixture(data)
    except FixtureError as error:
        raise HTTPException(400, str(error)) from None
    try:
        record = build_record(
            prepared, stored_at=datetime.now(UTC), app_version=__version__
        )
        with SqliteInvestigationStore.open(
            settings.app_store_path, create=True
        ) as store:
            saved, created = store.add(record)
    except (StoreError, ValueError):
        raise HTTPException(503, "The investigation could not be stored.") from None
    receipt = json_document({"created": created, **saved.summary.as_json()})
    return saved.summary.investigation_id, created, receipt


@router.post(
    "",
    dependencies=[Depends(require_write)],
    summary="Save an investigation of one inline fictional fixture",
    responses={200: {"description": "Same input already saved"}, 201: {}},
)
async def create_investigation(request: Request) -> Response:
    """Save a new record (201) or return the existing one for the same input (200)."""
    data = await _read_body(request)
    identifier, created, receipt = await run_in_threadpool(
        _save, _settings(request), data
    )
    response = _json(receipt, 201 if created else 200)
    response.headers["Location"] = f"/investigations/{identifier}"
    return response


@router.get(
    "", dependencies=[Depends(require_read)], summary="List saved investigations"
)
def list_investigations(
    request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> Response:
    """Return the newest summaries and the total count."""
    store = _open_for_reading(_settings(request))
    if store is None:
        return _json(json_document({"total": 0, "investigations": []}))
    try:
        with store:
            items = [item.as_json() for item in store.recent(limit)]
            total = store.count()
    except StoreError:
        raise HTTPException(503, "The investigation store is unavailable.") from None
    return _json(json_document({"total": total, "investigations": items}))


@router.get(
    "/{investigation_id}",
    dependencies=[Depends(require_read)],
    summary="Read the stored report exactly as saved",
)
def read_investigation(request: Request, investigation_id: str) -> Response:
    """Return the stored report bytes after the integrity check."""
    record = _record(_settings(request), investigation_id)
    if integrity_problems(record):
        raise HTTPException(409, "The stored investigation failed its integrity check.")
    return _json(record.report_document)


@router.get(
    "/{investigation_id}/verification",
    dependencies=[Depends(require_read)],
    summary="Check hashes and rebuild the report from the stored input",
)
def verify_investigation(request: Request, investigation_id: str) -> Response:
    """Return every verification check; the HTTP status is 200 either way."""
    result = verify_record(_record(_settings(request), investigation_id))
    return _json(json_document(result))


@router.post(
    "/{investigation_id}/agent",
    dependencies=[Depends(require_read)],
    summary="Run the bounded offline agent on a verified investigation",
)
def run_investigation_agent(request: Request, investigation_id: str) -> Response:
    """Return the agent report; nothing is stored or executed."""
    record = _record(_settings(request), investigation_id)
    if verify_record(record)["status"] != "verified":
        raise HTTPException(409, "The stored investigation did not verify.")
    try:
        view = build_view(decode_input(record.input_document), investigation_id)
        report = run_agent(view, OfflineRulesProvider())
    except (AgentError, ValueError):
        raise HTTPException(
            409, "The agent could not run on this investigation."
        ) from None
    return _json(json_document(report))
