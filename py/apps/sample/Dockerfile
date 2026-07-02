# Build from the py/ workspace root so the workspace lockfile and shared common
# libs are in the build context:
#   docker build -f apps/sample/Dockerfile -t fdebench-triage .
# Run (provide Azure OpenAI settings via an env file):
#   docker run --rm -p 8000:8000 --env-file apps/sample/.env fdebench-triage

ARG PYTHON_VERSION=3.12

# ── Build stage ───────────────────────────────────────────────────────────────
# Resolves the `sample` package (and its deps) into an isolated venv using a
# pinned uv release for reproducible installs.
FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

WORKDIR /code

# Workspace-level lockfile and root pyproject needed for uv workspace resolution.
COPY pyproject.toml uv.lock ./

# App source and shared common libraries (workspace members).
COPY apps/sample/ ./apps/sample/
COPY common/ ./common/

# Install only the sample app and its dependencies into /code/.venv.
RUN uv sync --package sample --frozen --no-cache

# ── Runtime stage ─────────────────────────────────────────────────────────────
# Copies only the venv and source — no build tooling, no uv binary.
FROM python:${PYTHON_VERSION}-slim AS runtime

WORKDIR /code

# Copy the pre-built venv from the builder stage.
COPY --from=builder /code/.venv /code/.venv

# Copy the app source and shared libs (needed at runtime for imports).
COPY --from=builder /code/apps/sample/ ./apps/sample/
COPY --from=builder /code/common/ ./common/

# Python resolution: app module directory + common lib src trees. The sample app
# imports the shared libs via PYTHONPATH (mirrors the Makefile).
ENV PYTHONPATH=/code/apps/sample:/code/common/libs/models/src:/code/common/libs/fastapi/src:/code/common/libs/fdebenchkit/src
# Put the venv on PATH so `uvicorn` resolves without `uv run`.
ENV PATH=/code/.venv/bin:$PATH
ENV PYTHONUNBUFFERED=1

WORKDIR /code/apps/sample
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
