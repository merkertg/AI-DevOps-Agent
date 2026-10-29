"""Map annotated deployment tags of the allowlisted lab repository to evidence."""

import json
import re
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Protocol

from app.application.inputs import DeploymentEvidence
from app.domain import Incident, Observation, ObservationKind, Source, SourceKind
from app.infrastructure.redaction import sanitize_text

DEPLOYMENT_RECORD = "ai-devops-agent.deployment"
DEPLOYMENT_RECORD_VERSION = 1
OBJECT_ID = re.compile(r"[0-9a-f]{40}\Z")
TAG_LEAF = re.compile(r"[0-9]{4,8}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
_RECORD_FIELDS = {"record", "version", "service", "environment", "deployed_at"}


class HistoryError(ValueError):
    """Refuse an out-of-scope history request with a safe static message."""


class HistoryUnavailable(Exception):
    """Carry a safe gap text explaining why history could not be read."""


@dataclass(frozen=True, slots=True)
class RepositoryScope:
    """Name the only repository, service, environment and tag namespace to read."""

    name: str
    service: str
    environment: str
    root_commit: str
    tag_prefix: str

    def __post_init__(self) -> None:
        """Require slugs, a full root commit ID and a deploy/<environment>/ prefix."""
        names = (self.name, self.service, self.environment)
        if not all(_SLUG.fullmatch(value) for value in names):
            raise ValueError("Repository scope names must be lowercase slugs.")
        if not OBJECT_ID.fullmatch(self.root_commit):
            raise ValueError("The root commit must be a full lowercase object ID.")
        if self.tag_prefix != f"deploy/{self.environment}/":
            raise ValueError("The tag prefix must be deploy/<environment>/.")


# Changing which repository may be read is a reviewed code change (ADR 0004).
# The root commit identifies the fictional repository built by lab/stage05.
STAGE05_REPOSITORY = RepositoryScope(
    name="training-api-lab",
    service="training-api",
    environment="lab",
    root_commit="2c4523d0987e737cea096d11292bef257b520141",
    tag_prefix="deploy/lab/",
)


@dataclass(frozen=True, slots=True)
class HistoryLimits:
    """Bound tag count and the sizes of objects and messages read from Git."""

    max_records: int = 100
    max_tag_bytes: int = 4_096
    max_commit_bytes: int = 65_536
    max_message_chars: int = 2_048

    def __post_init__(self) -> None:
        """Reject booleans, nonintegers and nonpositive resource limits."""
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value <= 0:
                raise ValueError("History limits must be positive integers.")


DEFAULT_HISTORY_LIMITS = HistoryLimits()


@dataclass(frozen=True, slots=True)
class DeploymentTag:
    """Hold one annotated tag and the commit fields read for it."""

    name: str
    tag_object: str
    message: str
    recorded_at: datetime
    revision: str
    committed_at: datetime
    summary: str


@dataclass(frozen=True, slots=True)
class TagListing:
    """Return bounded tags plus gaps for tags that could not be used."""

    tags: tuple[DeploymentTag, ...] = ()
    gaps: tuple[str, ...] = ()


class DeploymentReader(Protocol):
    """Read deployment tags of one allowlisted repository without changing it."""

    def read_tags(self, scope: RepositoryScope, limits: HistoryLimits) -> TagListing:
        """Return bounded tags; raise HistoryUnavailable or HistoryError."""
        ...


def tag_label(leaf: str) -> str:
    """Name a tag in gap text only when its leaf is a safe sequence number."""
    if TAG_LEAF.fullmatch(leaf):
        return f"Deployment tag {leaf}"
    return "A deployment tag with an unsupported name"


def parse_record(message: str) -> tuple[str, str, datetime] | None:
    """Accept only the exact structured record; any other tag text is not a deploy."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        """Treat repeated keys as ambiguous instead of letting the last one win."""
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate key")
        return dict(pairs)

    try:
        value: object = json.loads(message, object_pairs_hook=unique)
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or value.keys() != _RECORD_FIELDS:
        return None
    service = value["service"]
    environment = value["environment"]
    deployed = value["deployed_at"]
    if (
        value["record"] != DEPLOYMENT_RECORD
        or type(value["version"]) is not int
        or value["version"] != DEPLOYMENT_RECORD_VERSION
        or not isinstance(service, str)
        or not isinstance(environment, str)
        or not isinstance(deployed, str)
    ):
        return None
    try:
        deployed_at = datetime.fromisoformat(deployed)
        if deployed_at.utcoffset() is None:
            return None
        return service, environment, deployed_at.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def collect_deployments(
    reader: DeploymentReader,
    incident: Incident,
    *,
    scope: RepositoryScope = STAGE05_REPOSITORY,
    limits: HistoryLimits = DEFAULT_HISTORY_LIMITS,
) -> DeploymentEvidence:
    """Turn verified deployment records inside the window into sanitized evidence."""
    if incident.service != scope.service:
        raise HistoryError("The allowlisted repository belongs to another service.")
    source = Source(
        f"{scope.name}-deployments", scope.service, SourceKind.DEPLOYMENT_HISTORY
    )
    try:
        listing = reader.read_tags(scope, limits)
    except HistoryUnavailable as error:
        return DeploymentEvidence(scope.name, source, gaps=(str(error),))
    observations: list[Observation] = []
    gaps = list(listing.gaps)
    truncated: list[str] = []
    redactions = 0
    for tag in listing.tags:
        leaf = tag.name.removeprefix(scope.tag_prefix)
        label = tag_label(leaf)
        if not tag.name.startswith(scope.tag_prefix) or not TAG_LEAF.fullmatch(leaf):
            gaps.append(f"{label} was skipped.")
            continue
        record = parse_record(tag.message)
        if record is None:
            gaps.append(f"{label} has no valid deployment record; it was skipped.")
            continue
        service, environment, deployed_at = record
        if service != scope.service or environment != scope.environment:
            gaps.append(
                f"{label} names another service or environment; it was skipped."
            )
            continue
        if deployed_at < tag.committed_at:
            gaps.append(
                f"{label} claims a deployment before its commit was created; "
                "it was skipped."
            )
            continue
        if tag.recorded_at < deployed_at:
            gaps.append(
                f"{label} was recorded before its claimed deployment time; "
                "it was skipped."
            )
            continue
        if not incident.starts_at <= deployed_at <= incident.ends_at:
            continue
        message, count, cut = sanitize_text(
            f"Deployment record {tag.name} (tag object {tag.tag_object}): "
            f"service={scope.service} environment={scope.environment} "
            f"revision={tag.revision} "
            f"committed_at={tag.committed_at.astimezone(UTC).isoformat()} "
            f"recorded_at={tag.recorded_at.astimezone(UTC).isoformat()}. "
            f"Commit summary: {tag.summary}",
            limits.max_message_chars,
        )
        observation_id = f"{source.source_id}-{leaf}"
        observations.append(
            Observation(
                observation_id,
                source.source_id,
                deployed_at,
                tag.recorded_at,
                ObservationKind.DEPLOYMENT,
                message=message,
            )
        )
        redactions += count
        if cut:
            truncated.append(observation_id)
    observations.append(
        Observation(
            f"{source.source_id}-summary",
            source.source_id,
            incident.ends_at,
            incident.ends_at,
            ObservationKind.NOTE,
            message=(
                f"Deployment history: examined {len(listing.tags)} annotated tags "
                f"under refs/tags/{scope.tag_prefix} in repository {scope.name} "
                f"(root commit {scope.root_commit}); deployments used inside the "
                f"incident window: {len(observations)}."
            ),
        )
    )
    return DeploymentEvidence(
        scope.name,
        source,
        tuple(observations),
        tuple(gaps),
        tuple(sorted(truncated)),
        redactions,
    )
