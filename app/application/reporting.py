"""Build deterministic offline reports from prepared evidence without external I/O."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import cast

from app.application.inputs import Origin, PreparedInput
from app.domain import investigate

_COMMON_LIMITATIONS = (
    "Collection completeness is a producer declaration, not independently verified.",
    "Only documented redaction patterns are removed; arbitrary secrets may remain.",
    "Structured signals and reference resolution do not prove a root cause.",
)
# The scope sentence depends on whether deployment history was read for this input.
_SCOPE_LIMITATIONS: dict[tuple[Origin, bool], str] = {
    ("fixture", False): (
        "No live infrastructure, deployment history, model or telemetry was queried."
    ),
    ("fixture", True): "No live infrastructure, model or telemetry was queried.",
    ("docker_lab", False): (
        "Docker state and logs were read once from allowlisted lab containers; "
        "deployment history, models and telemetry were not queried."
    ),
    ("docker_lab", True): (
        "Docker state and logs were read once from allowlisted lab containers; "
        "models and telemetry were not queried."
    ),
}
_ORIGIN_LIMITATIONS: dict[Origin, tuple[str, ...]] = {
    "fixture": (),
    "docker_lab": (
        "Log times are Docker daemon receipt times; Docker state shows only the "
        "latest container exit.",
        "Signals come from Docker state and the application's structured event "
        "field; they are reports, not independent proof.",
    ),
}
_TELEMETRY_LIMITATIONS = (
    "Metrics come only from allowlisted Prometheus queries and traces from a "
    "bounded OTLP export; they show symptoms, not causes.",
    "Signals at the same time are correlated, not causally linked; source clocks "
    "and collection delays can shift their order.",
)
_HISTORY_LIMITATIONS = (
    "Deployment records are annotated tags in the allowlisted lab repository; "
    "they are recorded claims, not proof of what ran.",
    "Only deployments recorded inside the incident window are included; commit "
    "time is never used as deployment time.",
)


def _scope_limitation(origin: Origin, history: bool, telemetry: bool) -> str:
    """State which inputs were read; earlier wording stays unchanged."""
    if not telemetry:
        return _SCOPE_LIMITATIONS[(origin, history)]
    first = (
        "Docker state and logs were read once from allowlisted lab containers"
        if origin == "docker_lab"
        else "No live container was queried"
    )
    rest = (
        "models were not queried"
        if history
        else ("deployment history and models were not queried")
    )
    return f"{first}; telemetry was read from the given sources; {rest}."


def build_report(prepared: PreparedInput) -> dict[str, object]:
    """Investigate sanitized inputs and expose evidence gaps alongside every finding."""
    investigation = investigate(
        prepared.incident, prepared.sources, prepared.observations
    )
    observed_sources = {event.source_id for event in investigation.timeline}
    missing_sources = [
        source.source_id
        for source in investigation.sources
        if source.source_id not in observed_sources
    ]
    unknowns = ["The root cause is not established by these observations."]
    if prepared.declared_complete is None:
        unknowns.append("Collection completeness was not declared.")
    elif not prepared.declared_complete:
        unknowns.append("The producer reports incomplete collection.")
    if prepared.declared_truncated:
        unknowns.append("The producer reports truncated input.")
    if prepared.gaps:
        unknowns.append("Collector-reported gaps remain.")
    if prepared.truncated_observation_ids:
        unknowns.append("Some evidence messages were truncated after redaction.")
    if prepared.truncated_gap_count:
        unknowns.append("Some gap descriptions were truncated after redaction.")
    if not investigation.timeline:
        unknowns.append("No observations were supplied.")
    if missing_sources:
        unknowns.append("Some declared sources have no observations.")
    if investigation.contradictions:
        unknowns.append("Contradictory container outcomes remain unresolved.")

    partial = (
        prepared.declared_complete is False
        or prepared.declared_truncated
        or bool(prepared.gaps)
        or bool(prepared.truncated_observation_ids)
        or bool(prepared.truncated_gap_count)
        or not investigation.timeline
        or bool(missing_sources)
        or bool(investigation.contradictions)
    )
    if partial:
        status = "partial"
    elif prepared.declared_complete is True:
        status = "declared_complete"
    else:
        status = "unknown"
    report: dict[str, object] = asdict(investigation)
    # Trace IDs appear only when known, so reports without telemetry keep
    # the exact bytes they had before Stage 07.
    for entry in cast(list[dict[str, object]], report["timeline"]):
        if entry["trace_id"] is None:
            del entry["trace_id"]
    report["schema_version"] = 1
    report["origin"] = prepared.origin
    report["data_quality"] = {
        "status": status,
        "declared_complete": prepared.declared_complete,
        "declared_truncated": prepared.declared_truncated,
        "gaps": list(prepared.gaps),
        "truncated_observation_ids": sorted(prepared.truncated_observation_ids),
        "truncated_gap_count": prepared.truncated_gap_count,
        "sources_without_observations": missing_sources,
        "redaction_count": prepared.redaction_count,
    }
    report["unknowns"] = unknowns
    history_read = prepared.history_repository is not None
    report["limitations"] = [
        *_COMMON_LIMITATIONS,
        _scope_limitation(prepared.origin, history_read, bool(prepared.telemetry)),
        *_ORIGIN_LIMITATIONS[prepared.origin],
        *(_HISTORY_LIMITATIONS if history_read else ()),
        *(_TELEMETRY_LIMITATIONS if prepared.telemetry else ()),
    ]
    return report


def _json_default(value: object) -> str:
    """Serialize explicit domain timestamps while rejecting unsupported objects."""
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("Unsupported report field type")


def render_report(prepared: PreparedInput) -> str:
    """Render one stable ASCII-safe JSON document with no partial stdout writes."""
    return (
        json.dumps(
            build_report(prepared),
            default=_json_default,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
