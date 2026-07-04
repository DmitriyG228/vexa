"""gate:health — the vcs-executor service exposes a conforming liveness /health.

A pure liveness probe (process-up): no auth (mirrors the compose healthcheck), no redis op, no
GitHub or agent-api hop. 200 + {status:"ok", service:"vcs-executor"} means the process is up —
consumer configuration is a separate, loudly-logged concern (fail-closed: unconfigured ⇒ no
consumption, never a crash-loop).
"""
import httpx
from fastapi.testclient import TestClient

from vcs_executor import create_app


def _client(agent_api, github, redis_client) -> TestClient:
    return TestClient(create_app(
        "http://agent-api.test",
        redis_client=redis_client,
        agent_api_transport=httpx.MockTransport(agent_api.handler),
        github_transport=httpx.MockTransport(github.handler),
        consume=False,
    ))


def test_health_ok(agent_api, github, redis_client):
    r = _client(agent_api, github, redis_client).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "vcs-executor"


def test_health_makes_no_downstream_hop(agent_api, github, redis_client):
    """Health must be reachable with no credential and must touch neither GitHub nor agent-api."""
    assert _client(agent_api, github, redis_client).get("/health").status_code == 200
    assert agent_api.requests == []
    assert github.requests == []
