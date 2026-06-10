FROM python:3.11-slim

WORKDIR /app

# git: ei-lineage (#24) manages the per-org knowledge workspaces (git repos on
# the /workspaces volume) from inside this service.
RUN apt-get update && apt-get install -y --no-install-recommends gcc curl ca-certificates git \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

COPY services/agent-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/agent-api .

# Org workspace seed template (ei-lineage #24) — synthetic content only.
# Copied to /workspaces/<org>/repo on an org's first meeting.completed event.
# (Already included by the COPY above; pinned env in compose: EI_WORKSPACE_SEED_PATH.)

CMD ["uvicorn", "agent_api.main:app", "--host", "0.0.0.0", "--port", "8100"]
