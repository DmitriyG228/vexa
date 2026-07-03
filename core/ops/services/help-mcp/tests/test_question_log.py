"""L3 — the help:questions telemetry stream: every tool call XADDs one shaped entry
(MAXLEN ~5000, approximate), review_question_log reads them back (since/limit), and redis
absence DEGRADES — the answer always returns, the log read reports a note."""
from fastapi.testclient import TestClient

from help_mcp.telemetry import QUESTIONS_MAXLEN, QUESTIONS_STREAM

from conftest import BrokenRedis

FIELDS = {"ts", "tool", "question", "top_paths", "answered_from_docs"}


def test_every_tool_call_lands_one_shaped_entry(client: TestClient, redis_client):
    client.post("/search-docs", json={"query": "docker compose quick start"})
    client.get("/deploy-guide", params={"target": "compose"})
    client.get("/known-issues")
    client.post("/escalate", json={"question": "why?"})
    client.get("/question-log")
    tools = [args[1]["tool"] for args, _ in redis_client.xadd_calls]
    assert tools == ["search_vexa_docs", "get_deploy_guide", "check_known_issues",
                     "escalate", "review_question_log"]
    for args, kwargs in redis_client.xadd_calls:
        assert args[0] == QUESTIONS_STREAM
        assert set(args[1]) == FIELDS
        assert kwargs == {"maxlen": QUESTIONS_MAXLEN, "approximate": True}


def test_answered_from_docs_reflects_the_hit(client: TestClient, redis_client):
    client.post("/search-docs", json={"query": "docker compose quick start"})
    client.post("/search-docs", json={"query": "zzz qqq xyzzy"})
    hit, miss = [args[1] for args, _ in redis_client.xadd_calls]
    assert hit["answered_from_docs"] == "true" and hit["top_paths"] != "[]"
    assert miss["answered_from_docs"] == "false" and miss["top_paths"] == "[]"


def test_review_reads_back_oldest_first(client: TestClient):
    client.post("/search-docs", json={"query": "api key"})
    client.get("/known-issues")
    body = client.get("/question-log").json()
    entries = body["results"]
    assert [e["tool"] for e in entries] == ["search_vexa_docs", "check_known_issues"]
    first = entries[0]
    assert first["provenance"] == "operational"
    assert first["question"] == "api key"
    assert first["top_paths"] == ["auth.mdx"]
    assert first["answered_from_docs"] is True
    assert body["stream"] == QUESTIONS_STREAM


def test_limit_keeps_the_newest(client: TestClient):
    for i in range(5):
        client.post("/search-docs", json={"query": f"api key {i}"})
    entries = client.get("/question-log", params={"limit": 2}).json()["results"]
    assert len(entries) == 2
    assert [e["question"] for e in entries] == ["api key 3", "api key 4"]


def test_since_filters_and_rejects_garbage(client: TestClient):
    client.post("/search-docs", json={"query": "api key"})
    assert client.get("/question-log", params={"since": "2000-01-01T00:00:00Z"}).json()["results"]
    assert client.get("/question-log", params={"since": "2100-01-01T00:00:00Z"}).json()["results"] == []
    assert client.get("/question-log", params={"since": "not-a-date"}).status_code == 422


def test_redis_absence_degrades_gracefully(app_factory):
    c = TestClient(app_factory(redis_client=BrokenRedis()))
    # the answer path is unaffected
    r = c.post("/search-docs", json={"query": "docker compose quick start"})
    assert r.status_code == 200 and r.json()["results"]
    # the read-back reports the outage instead of crashing
    body = c.get("/question-log").json()
    assert body["results"] == []
    assert any("question log unavailable" in n for n in body["notes"])
