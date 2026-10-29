"""Expose the Stage 02 offline incident model and deterministic investigation."""

from app.domain.investigation import investigate
from app.domain.models import (
    TRACE_ID,
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
    "TRACE_ID",
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
