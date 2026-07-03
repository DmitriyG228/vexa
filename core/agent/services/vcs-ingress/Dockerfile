# syntax=docker/dockerfile:1
# vcs-ingress (v0.12) — the untrusted-event ingress: GitHub App webhooks in, opaque refs out.
# Verifies X-Hub-Signature-256, persists ingress.v1 Deliveries to the vcs:events stream
# (persist-first), dispatches event.v1 envelopes to agent-api /events per vcs:subs subscription.
# No payload bytes cross the seam. Build context: agent/services/vcs-ingress (pyproject.toml +
# uv.lock + src/), mirroring the mcp service image.
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
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 2) Production-only runtime dep NOT in the (lean) test pyproject: the ASGI server.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0"

# 3) The shipped source (src/ is on PYTHONPATH; the package is not pip-installed).
COPY src ./src

EXPOSE 8020
# python -m vcs_ingress → uvicorn over create_app() (VEXA_AGENT_API_URL / REDIS_URL /
# VEXA_GITHUB_WEBHOOK_SECRET / HOST / PORT from env).
CMD ["python", "-m", "vcs_ingress"]
