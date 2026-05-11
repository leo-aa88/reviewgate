# Runtime image for the hosted ReviewGate app (FastAPI API + optional Dramatiq worker).
# The deterministic engine alone does not need this image; use `pip install reviewgate` for that.
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Project metadata and installable packages
COPY pyproject.toml LICENSE NOTICE README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic/

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -e ".[app]"

RUN useradd --create-home --uid 1000 reviewgate \
    && chown -R reviewgate:reviewgate /app

USER reviewgate

EXPOSE 8000

# Override with e.g. `docker run … reviewgate:local reviewgate-worker`
CMD ["reviewgate-api"]
