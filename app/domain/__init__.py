"""Expose the Stage 02 offline incident model and deterministic investigation."""

from app.domain.investigation import investigate
from app.domain.models import (
    Contradiction,
    EvidenceReference,
    Fact,
    Hypothesis,
    Incident,
    Investigation,
    Observation,
    ObservationKind,
    Source,
    SourceKind,
)

__all__ = [
    "Contradiction",
    "EvidenceReference",
    "Fact",
    "Hypothesis",
    "Incident",
    "Investigation",
    "Observation",
    "ObservationKind",
    "Source",
    "SourceKind",
    "investigate",
]
