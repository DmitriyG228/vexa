"""Agent Runtime configuration from environment variables."""

import os

# Server
PORT = int(os.getenv("AGENT_RUNTIME_PORT", os.getenv("CHAT_API_PORT", "8100")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")

# Runtime API (container lifecycle)
RUNTIME_API_URL = os.getenv("RUNTIME_API_URL", "http://runtime-api:8090")

# Admin API (user data, config)
ADMIN_API_URL = os.getenv("ADMIN_API_URL", "http://admin-api:8001")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")

# Container defaults
AGENT_IMAGE = os.getenv("AGENT_IMAGE", "vexaai/vexa-agent:latest")
DOCKER_NETWORK = os.getenv("DOCKER_NETWORK", "")
CONTAINER_PREFIX = os.getenv("CONTAINER_PREFIX", "agent-")
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "300"))

# Auth
API_KEY = os.getenv("API_KEY", "")
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")

# Self-reference URL (for scheduler callback targets)
AGENT_API_INTERNAL_URL = os.getenv("AGENT_API_INTERNAL_URL", "http://agent-api:8100")

# Anthropic API key (passed to agent containers for Claude CLI auth)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Claude credential files (mounted into agent containers for OAuth auth)
CLAUDE_CREDENTIALS_PATH = os.getenv("CLAUDE_CREDENTIALS_PATH", "")
CLAUDE_JSON_PATH = os.getenv("CLAUDE_JSON_PATH", "")

# Workspace / S3
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")  # "local" or "s3"
WORKSPACE_PATH = os.getenv("WORKSPACE_PATH", "/workspace")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "workspaces")

# Agent CLI
AGENT_CLI = os.getenv("AGENT_CLI", "claude")
AGENT_ALLOWED_TOOLS = os.getenv("AGENT_ALLOWED_TOOLS", "Read,Write,Edit,Bash,Glob,Grep")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "")
AGENT_WORKSPACE_PATH = os.getenv("AGENT_WORKSPACE_PATH", "/root/.claude/projects/-workspace")
AGENT_STREAM_FORMAT = os.getenv("AGENT_STREAM_FORMAT", "stream-json")

# CORS
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# ── Enterprise-Intelligence lineage (pack ei-lineage, issue #24) ───────────
# Transcript source — meeting-api serves the collector's internal endpoint.
TRANSCRIPTION_COLLECTOR_URL = os.getenv("TRANSCRIPTION_COLLECTOR_URL", "http://meeting-api:8080")
# Org knowledge workspaces (git repos) live on a volume, one dir per org.
EI_WORKSPACES_PATH = os.getenv("EI_WORKSPACES_PATH", "/workspaces")
# Seed template baked into the image (synthetic content only).
EI_WORKSPACE_SEED_PATH = os.getenv("EI_WORKSPACE_SEED_PATH", "/app/workspace-seed")
# Optional full agent command override (provider via env only — no hardcoding).
# When empty, the command is built from AGENT_CLI/AGENT_ALLOWED_TOOLS/DEFAULT_MODEL.
EI_AGENT_CMD = os.getenv("EI_AGENT_CMD", "")
# Wall-clock budget for one proposal agent run (seconds).
EI_AGENT_TIMEOUT = int(os.getenv("EI_AGENT_TIMEOUT", "900"))
