# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.28 AS uv
FROM python:3.14.6-slim AS runtime

COPY --from=uv /uv /uvx /bin/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable \
    && groupadd --gid 10001 atlas \
    && useradd --uid 10001 --gid atlas --no-create-home --shell /usr/sbin/nologin atlas

USER atlas

EXPOSE 8000

FROM runtime AS temporal-worker

CMD ["atlas-temporal-worker"]

FROM runtime AS fixture-worker

CMD ["atlas-fixture-worker"]

FROM runtime AS task-intent-consumer

CMD ["atlas-task-intent-consumer"]

FROM runtime AS task-gate-callback-consumer

CMD ["atlas-task-gate-callback-consumer"]

FROM runtime AS task-schedule-worker

CMD ["atlas-task-schedule-worker"]

FROM runtime AS task-worker

CMD ["atlas-task-worker"]

FROM runtime AS auth-session-worker

USER root

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN playwright install --with-deps chromium \
    && chown -R atlas:atlas /ms-playwright

USER atlas

CMD ["atlas-auth-session-worker"]

FROM runtime AS browser-worker

USER root

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN playwright install --with-deps chromium \
    && chown -R atlas:atlas /ms-playwright

USER atlas

CMD ["atlas-browser-worker"]

FROM runtime AS migrations

COPY --chown=atlas:atlas alembic.ini ./
COPY --chown=atlas:atlas migrations ./migrations

CMD ["alembic", "upgrade", "head"]

FROM runtime AS api

CMD ["uvicorn", "atlas_testops.main:app", "--host", "0.0.0.0", "--port", "8000"]
