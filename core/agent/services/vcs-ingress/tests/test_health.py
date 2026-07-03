"""gate:health — the vcs-ingress service exposes a conforming liveness /health.

A pure liveness probe (process-up): no auth (mirrors the compose healthcheck), no redis op, no
agent-api hop. 200 + {status:"ok", service:"vcs-ingress"} means the service process is up.
"""
from fastapi.testclient import TestClient


def test_health_ok(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "vcs-ingress"


def test_health_makes_no_downstream_hop(client: TestClient, agent_api):
    """Health must be reachable with no credential and must not touch agent-api."""
    assert client.get("/health").status_code == 200
    assert agent_api.requests == []
