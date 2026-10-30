# syntax=docker/dockerfile:1

# Both stages use the same Python, so the virtual environment copies cleanly.
# Images are pinned by index digest (all architectures); update with
# docker buildx imagetools inspect <image>:<tag>.
FROM python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The builder installs only runtime dependencies from the locked file.
FROM python-base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 /uv /bin/uv

WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# The runtime image has no uv, build cache, tests, fixtures or lab files.
FROM python-base AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    APP_ENV=production \
    APP_STORE_PATH=/data/investigations.sqlite3

# A fixed non-root user owns only the application and the data directory.
RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app && \
    mkdir /data && chown app:app /data
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app app ./app
USER app
VOLUME ["/data"]

EXPOSE 8004

# Liveness only, through the standard library: curl is not installed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8004/health', timeout=3); assert response.status == 200 and json.load(response)['status'] == 'ok'"]

# The exec form passes stop signals straight to Uvicorn.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
