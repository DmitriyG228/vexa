# syntax=docker/dockerfile:1
# help-mcp (v0.12) — the PUBLIC help companion (MCP): answers deploy/integration questions from
# two always-labeled tiers — "doc" (the vendored docs corpus, cited by path + heading) and
# "operational" (live GitHub triage state). Read-only v1: its only writes are the help:questions
# telemetry stream and (config-gated, HELP_GITHUB_TOKEN) escalation issues.
#
# Build context: the v0.12 carve root (compose `context: ../..`, the vcs-executor idiom) — wider
# than the service dir because the image VENDORS the shipped docs corpus (docs/docs → /app/docs),
# the "doc" tier every cited answer comes from. DOCS_ROOT defaults to that vendored copy.
FROM python:3.12-slim

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# 1) Resolve + install the pinned dependency set from the lockfile (no project — package=false).
COPY core/ops/services/help-mcp/pyproject.toml core/ops/services/help-mcp/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 2) Production-only runtime dep NOT in the (lean) test pyproject: the ASGI server.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0"

# 3) The shipped source (src/ is on PYTHONPATH; the package is not pip-installed).
COPY core/ops/services/help-mcp/src ./src

# 4) The shipped docs corpus — the "doc" tier. Indexed in-memory at startup; DOCS_ROOT
#    (default /app/docs) points here, so every "doc" answer cites exactly what this image ships.
COPY docs/docs ./docs

EXPOSE 8011
# python -m help_mcp → uvicorn over create_app() (DOCS_ROOT / HELP_GITHUB_TOKEN /
# VEXA_OPS_SUBJECT / VEXA_AGENT_API_URL / REDIS_URL / HOST / PORT from env).
CMD ["python", "-m", "help_mcp"]
