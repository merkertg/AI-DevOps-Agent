# Project structure

[README](../../README.md) · [Detailed explanation](../STAGE_01.md)

## Implemented in Stage 01

| Location | Responsibility |
| --- | --- |
| `app/__init__.py` | Application version `0.1.0` |
| `app/config.py` | Validated `Settings` and cached `get_settings()` |
| `app/main.py` | `HealthResponse`, `create_app()`, liveness handler and exported `app` |
| `tests/` | Isolated behavior checks for the implemented foundation |
| `scripts/check_docs.py` | Local Markdown target validation |
| `pyproject.toml` | Project metadata, dependency constraints and tool configuration |
| `uv.lock` | Resolved dependency versions |
| `.python-version` | Python 3.12 selection |
| `.env.example` | Non-secret configuration example |
| `.github/workflows/ci.yml` | Automated checks for the project |
| `AGENTS.md` | Rules for future changes |
| `README.md` | Entry point, current scope and commands |
| `docs/STAGE_01.md` | Detailed Russian learning guide |
| `docs/ROADMAP.md` | Planned stages and completion criteria |
| `docs/APPROACH.md`, `docs/adr/` | Design principles and recorded decisions |
| `docs/instructions/` | Short English operating instructions |
| `docs/CI_CD.md` | CI and the per-stage commit/push procedure |
| `docs/DEVELOPMENT_LOG.md` | Actual changes |
| `docs/VERIFICATION.md`, `docs/guide/verification.json` | Factual checks and limitations |
| `docs/guide/README.md` | Learning material index |

The request path is `client → Uvicorn → FastAPI → health → HealthResponse → JSON`.
Application instances receive their settings through `create_app()`.

## Planned responsibilities

Create new layers only when they own behavior:

| Future location | First responsibility |
| --- | --- |
| `app/domain/` | Incidents, evidence, timeline and deterministic findings |
| `app/application/` | Investigation use cases and ports |
| `app/infrastructure/` | Bounded input, Docker, storage, Git and model adapters |
| Lab configuration | A disposable Docker application and controlled incidents |
| Evaluation fixtures | Known observations and independent expected results |

These locations are design directions, not implemented directories. The original
stack remains planned: Docker for the lab, GitPython for history, LangGraph for
bounded orchestration, Prometheus and OpenTelemetry for correlation. See the
[roadmap](../ROADMAP.md) for their order and completion criteria.
