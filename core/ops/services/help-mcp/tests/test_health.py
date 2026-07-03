"""gate:health — the help companion exposes a conforming liveness /health.

A pure liveness probe (process-up): no auth (mirrors the compose healthcheck), no GitHub or
agent-api hop, no telemetry write. 200 + {status:"ok", service:"help-mcp"} means the service
process is up. gate:health discovers this package (it builds a FastAPI app) and runs this eval.
"""
from fastapi.testclient import TestClient


def test_health_ok(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "help-mcp"


def test_health_makes_no_downstream_hop(client: TestClient, gh, agent_api, redis_client):
    """Health must not touch GitHub, agent-api, or the question log."""
    assert client.get("/health").status_code == 200
    assert gh.requests == []
    assert agent_api.requests == []
    assert redis_client.xadd_calls == []
