"""Carry an immutable, sanitized evidence snapshot into report generation."""

from dataclasses import dataclass, replace
from typing import Literal

from app.domain import Incident, Investigation, Observation, Source, SourceKind

Origin = Literal["fixture", "docker_lab"]
ORIGINS: tuple[Origin, ...] = ("fixture", "docker_lab")


@dataclass(frozen=True, slots=True)
class PreparedInput:
    """Retain normalized evidence and explicit producer/adapter quality metadata."""

    incident: Incident
    sources: tuple[Source, ...]
    observations: tuple[Observation, ...]
    declared_complete: bool | None = None
    declared_truncated: bool = False
    gaps: tuple[str, ...] = ()
    truncated_observation_ids: tuple[str, ...] = ()
    truncated_gap_count: int = 0
    redaction_count: int = 0
    origin: Origin = "fixture"
    history_repository: str | None = None

    def __post_init__(self) -> None:
        """Snapshot caller collections and require a known evidence origin."""
        for name in ("sources", "observations", "gaps", "truncated_observation_ids"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.origin not in ORIGINS:
            raise ValueError("origin must be a supported evidence origin")
        if self.history_repository is not None and (
            not isinstance(self.history_repository, str)
            or not self.history_repository.strip()
        ):
            raise ValueError("history_repository must be a non-empty string or None")


@dataclass(frozen=True, slots=True)
class DeploymentEvidence:
    """Hold sanitized deployment observations read from one allowlisted repository."""

    repository: str
    source: Source
    observations: tuple[Observation, ...] = ()
    gaps: tuple[str, ...] = ()
    truncated_observation_ids: tuple[str, ...] = ()
    redaction_count: int = 0

    def __post_init__(self) -> None:
        """Snapshot collections and require one deployment history source."""
        for name in ("observations", "gaps", "truncated_observation_ids"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.source.kind is not SourceKind.DEPLOYMENT_HISTORY:
            raise ValueError("deployment evidence requires a deployment history source")
        if any(event.source_id != self.source.source_id for event in self.observations):
            raise ValueError("deployment observations must use the history source")


def add_deployment_evidence(
    prepared: PreparedInput, evidence: DeploymentEvidence
) -> PreparedInput:
    """Merge deployment history into one snapshot and revalidate scope and identity."""
    if prepared.history_repository is not None:
        raise ValueError("deployment history was already added")
    snapshot = Investigation(
        prepared.incident,
        (*prepared.sources, evidence.source),
        (*prepared.observations, *evidence.observations),
    )
    return replace(
        prepared,
        sources=snapshot.sources,
        observations=snapshot.timeline,
        gaps=(*prepared.gaps, *evidence.gaps),
        truncated_observation_ids=tuple(
            sorted(
                (
                    *prepared.truncated_observation_ids,
                    *evidence.truncated_observation_ids,
                )
            )
        ),
        redaction_count=prepared.redaction_count + evidence.redaction_count,
        history_repository=evidence.repository,
    )
