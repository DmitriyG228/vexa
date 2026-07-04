# syntax=docker/dockerfile:1
# vcs-executor (v0.12) — the privileged half of the proposal.v1 human gate: the ONLY holder of
# GitHub credentials. XREADGROUPs human-approved proposals off proposal:approved, re-validates
# them (schema by path + policy, BEFORE any credential), mints a per-proposal GitHub App
# installation token scoped to the target repo + the proposal's level, executes exactly the
# approved action (L2 REST annotate; L3 git am/commit → push → PR), and reports the result to
# agent-api /internal/proposals/{id}/executed — agent-api stays the single writer of proposal
# state.
#
# Build context: the v0.12 carve root (compose `context: ../..`, the meeting-api idiom) — wider
# than vcs-ingress because the shipped consumer validates AT THE SEAM (P8): the sealed
# proposal.v1 schema is loaded BY PATH (walking up from the module to `agent/contracts/...`), so
# the frozen contract file is vendored into the image alongside src/.
FROM python:3.12-slim

# git — the L3 flow (shallow clone → am/commit → push) shells out to it.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# 1) Resolve + install the pinned dependency set from the lockfile (no project — package=false).
COPY core/agent/services/vcs-executor/pyproject.toml core/agent/services/vcs-executor/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 2) Production-only runtime dep NOT in the (lean) test pyproject: the ASGI server.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0"

# 3) The shipped source (src/ is on PYTHONPATH; the package is not pip-installed).
COPY core/agent/services/vcs-executor/src ./src

# 4) The sealed proposal.v1 schema, loaded BY PATH at consume time. Placed under /app/ so the
#    module walk-up (Path(__file__).resolve().parents → /app/src/vcs_executor/... → /app) resolves
#    `agent/contracts/proposal.v1/proposal.schema.json`.
COPY core/agent/contracts/proposal.v1/proposal.schema.json ./agent/contracts/proposal.v1/proposal.schema.json

EXPOSE 8021
# python -m vcs_executor → uvicorn over create_app() (VEXA_AGENT_API_URL / REDIS_URL /
# VEXA_GITHUB_APP_ID / VEXA_GITHUB_APP_PRIVATE_KEY_PATH / VEXA_EXECUTOR_RESULT_TOKEN /
# VEXA_PROTECTED_BRANCHES / HOST / PORT from env). The consumer thread rides the app lifespan.
CMD ["python", "-m", "vcs_executor"]
