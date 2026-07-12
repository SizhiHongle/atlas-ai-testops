# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.28 AS uv
FROM python:3.14.6-slim

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

CMD ["uvicorn", "atlas_testops.main:app", "--host", "0.0.0.0", "--port", "8000"]
