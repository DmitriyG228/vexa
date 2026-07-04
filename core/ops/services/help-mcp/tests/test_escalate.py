"""L3 — escalate: the config-gated dual mode (token → filed issue; none → fully-formed draft),
the help.escalated event.v1 envelope in BOTH modes (validated BY SCHEMA PATH — the cross-package
idiom), and every degrade: GitHub write failure falls back to the draft; an unreachable
agent-api never blocks the escalation answer."""
from fastapi.testclient import TestClient

from conftest import validate_event

PAYLOAD = {
    "question": "Bots reach joining then die on an air-gapped compose install — what am I missing?",
    "environment_summary": "compose stack, Ubuntu 24.04, no internet egress, bot built from source",
    "docs_consulted": ["deployment.mdx#Air-gapped", "troubleshooting.mdx#Bot won't join the meeting"],
}


# ── draft mode (no HELP_GITHUB_TOKEN) ─────────────────────────────────────────────────────────
def test_draft_mode_returns_the_full_issue(client: TestClient, gh):
    body = client.post("/escalate", json=PAYLOAD).json()
    [result] = body["results"]
    assert result["provenance"] == "operational"
    assert result["mode"] == "draft"
    assert result["issue_url"] is None
    draft = result["draft"]
    assert draft["title"].startswith("[help] ")
    assert PAYLOAD["question"] in draft["body"]
    assert PAYLOAD["environment_summary"] in draft["body"]
    for doc in PAYLOAD["docs_consulted"]:
        assert f"- {doc}" in draft["body"]
    assert "filed via the vexa help companion" in draft["body"]
    assert gh.created == [], "draft mode must not touch GitHub"


def test_draft_mode_event_envelope_conforms_by_schema_path(client: TestClient, agent_api):
    result = client.post("/escalate", json=PAYLOAD).json()["results"][0]
    [envelope] = agent_api.bodies()
    validate_event(envelope)  # event.v1 BY PATH — never an agent-api import
    assert envelope["name"] == "help.escalated"
    assert envelope["subject"] == "ops"
    assert envelope["source"]["uri"].startswith("help://draft/")
    assert envelope["source"]["uri"] == result["event"]["source_uri"]
    assert result["event"]["delivered"] is True


# ── filed mode (HELP_GITHUB_TOKEN set) ────────────────────────────────────────────────────────
def test_filed_mode_creates_the_issue_and_returns_its_url(app_factory, gh, agent_api):
    c = TestClient(app_factory(github_token="tok-123"))
    [result] = c.post("/escalate", json=PAYLOAD).json()["results"]
    assert result["mode"] == "filed"
    assert result["issue_url"] == "https://github.com/Vexa-ai/vexa/issues/999"
    assert result["draft"] is None
    [created] = gh.created
    assert created["title"].startswith("[help] ")
    assert "## Environment" in created["body"]
    # the event rides the ISSUE URL as its opaque source ref
    [envelope] = agent_api.bodies()
    validate_event(envelope)
    assert envelope["source"]["uri"] == result["issue_url"]


def test_filed_mode_github_failure_falls_back_to_the_draft(app_factory):
    import httpx

    def failing(request):  # POST /issues fails; nothing else is hit in this flow
        return httpx.Response(500, json={"message": "boom"})

    c = TestClient(app_factory(github_token="tok-123", github_transport=httpx.MockTransport(failing)))
    body = c.post("/escalate", json=PAYLOAD).json()
    [result] = body["results"]
    assert result["mode"] == "draft"
    assert result["draft"] is not None
    assert any("could not file the issue" in n for n in body["notes"])


# ── the agent-api degrade ─────────────────────────────────────────────────────────────────────
def test_unreachable_agent_api_never_blocks_the_escalation(client: TestClient, agent_api):
    agent_api.status = 503
    body = client.post("/escalate", json=PAYLOAD).json()
    [result] = body["results"]
    assert result["mode"] == "draft"
    assert result["draft"] is not None, "the caller still gets the full draft"
    assert result["event"]["delivered"] is False
    assert any("ops-event delivery failed" in n for n in body["notes"])


def test_escalate_is_telemetered(client: TestClient, redis_client):
    client.post("/escalate", json=PAYLOAD)
    entry = next(args[1] for args, _ in redis_client.xadd_calls if args[1]["tool"] == "escalate")
    assert entry["question"] == PAYLOAD["question"]
    assert entry["answered_from_docs"] == "false"


def test_empty_question_is_rejected(client: TestClient):
    assert client.post("/escalate", json={"question": ""}).status_code == 422
