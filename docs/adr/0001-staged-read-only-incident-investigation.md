# ADR 0001: Staged read-only incident investigation

- Status: accepted
- Date: 2026-10-23

## Context

The project is a learning build for incident investigation using Python, Docker,
FastAPI, LangGraph, GitPython, Prometheus and OpenTelemetry. Implementing the full
stack first would make it difficult to tell whether an incorrect report came
from parsing, collection, timing, model behavior or orchestration. The neighboring
AI Forge projects use small stages with detailed walkthroughs and explicit checks.
The user requests a commit and push for every completed stage.

## Decision

Begin with Python 3.12, uv, FastAPI, validated settings and a liveness contract.
Use locked dependencies, isolated tests, Ruff, strict mypy and a documentation
checker. Keep the current code in `app/__init__.py`, `app/config.py` and
`app/main.py`; add layers only with their first useful behavior.

Build an offline investigation using fictional fixtures before adding Docker
collection, persistence, Git history and model orchestration. Domain findings
are deterministic; model hypotheses must cite validated evidence references.
The eventual agent receives bounded read-only tools. Shell execution, container
exec, restart, deployment and automatic repair are outside its tool set.

Use accessible Russian for stage guides, the roadmap, development log and
verification; use English for code, README, operational instructions and ADRs.
Keep actual checks separate from expectations and remote CI. Finish each stage
with documentation, checks, a focused commit and push, then confirm its remote
status.

## Consequences

The first stage does not demonstrate AI incident analysis. It creates a small
foundation that can be explained and verified independently. Later stages need
their own contracts and checks before a model can use them.

Read-only behavior must be enforced at adapter and access boundaries. A wrapper
that only calls read methods does not make a fully privileged Docker socket
safe to expose. The Docker lab stage must solve that boundary explicitly.

The roadmap preserves the target stack without adding unused dependencies or
empty architecture directories. Every delivered stage remains runnable without
unfinished future components.
