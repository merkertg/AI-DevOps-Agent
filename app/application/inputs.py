"""Carry an immutable, sanitized evidence snapshot into report generation."""

from dataclasses import dataclass

from app.domain import Incident, Observation, Source


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

    def __post_init__(self) -> None:
        """Snapshot caller collections so subsequent list changes cannot alter input."""
        for name in ("sources", "observations", "gaps", "truncated_observation_ids"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
