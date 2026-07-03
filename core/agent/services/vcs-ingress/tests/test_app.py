"""L3 seam — the full webhook flow against the SHIPPED app (fake agent-api + fakeredis).

verify → allowlist → dedupe → persist-first → per-subscription dispatch → replay. Every accept
persists an ingress.v1-conformant Delivery; every dispatch POSTs an event.v1-conformant envelope
carrying the OPAQUE ref only (the injection canary must never cross); every reject path is the
right status and leaves no state behind.
"""
from __future__ import annotations

import hashlib
import json

from conftest import (
    SECRET,
    fixture_payload,
    gh_post,
    stream_records,
    subscribe,
    validate_delivery,
    validate_event,
)


# ── the accept path ────────────────────────────────────────────────────────────────────────────
def test_verified_delivery_persists_then_dispatches(client, agent_api, redis_client):
    subscribe(redis_client, event="vcs.issue.opened", subject="u_jane")
    payload = fixture_payload("issues.opened.json")

    r = gh_post(client, "issues", payload, "d-1001")

    assert r.status_code == 202, r.text
    assert r.json() == {
        "delivery_id": "d-1001", "event": "vcs.issue.opened", "subscriptions": 1, "dispatched": 1,
    }
    # Persisted: one conformant Delivery, digest anchored to the exact raw body.
    records = stream_records(redis_client)
    assert len(records) == 1
    validate_delivery(records[0])
    assert records[0]["payload_digest"] == hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    # Dispatched: one event.v1 envelope to agent-api /events, subject + plan from the subscription.
    assert [req.url.path for req in agent_api.requests] == ["/events"]
    envelope = agent_api.bodies()[0]
    validate_event(envelope)
    assert envelope["subject"] == "u_jane"
    assert envelope["source"]["uri"] == "github://vexa-ai/vexa/issues/512"
    assert envelope["plan"]["prompt"].startswith("Fetch the item")


def test_no_payload_bytes_cross_the_seam(client, agent_api, redis_client):
    """The attacker-authored text in the webhook body must reach NEITHER the stored record NOR
    the dispatched envelope — only the opaque github:// ref crosses."""
    subscribe(redis_client, event="vcs.issue.opened")
    gh_post(client, "issues", fixture_payload("issues.opened.json"), "d-1002")

    stored = json.dumps(stream_records(redis_client))
    dispatched = json.dumps(agent_api.bodies())
    for canary in ("ignore previous instructions", "SYSTEM OVERRIDE", "git push"):
        assert canary.lower() not in stored.lower()
        assert canary.lower() not in dispatched.lower()


def test_fan_out_one_envelope_per_subscription(client, agent_api, redis_client):
    subscribe(redis_client, event="vcs.pr.opened", subject="u_jane", routine_id="rt_a")
    subscribe(redis_client, event="vcs.pr.opened", subject="u_bob", routine_id="rt_b",
              prompt="Review the PR at the ref against the style guide.")
    subscribe(redis_client, event="vcs.issue.opened", subject="u_eve", routine_id="rt_c")  # wrong event

    r = gh_post(client, "pull_request", fixture_payload("pull_request.opened.json"), "d-1003")

    assert r.json()["subscriptions"] == 2 and r.json()["dispatched"] == 2
    subjects = {b["subject"] for b in agent_api.bodies()}
    assert subjects == {"u_jane", "u_bob"}
    for envelope in agent_api.bodies():
        validate_event(envelope)


# ── the reject paths (fail-closed, no state left behind) ──────────────────────────────────────
def test_tampered_body_is_401_and_nothing_persists(client, agent_api, redis_client):
    payload = fixture_payload("issues.opened.json")
    body = json.dumps(payload).encode()
    from vcs_ingress import signature_of

    sig = signature_of(SECRET, body)  # valid signature for the ORIGINAL body…
    tampered = json.dumps(payload | {"action": "opened", "extra": "x"}).encode()  # …sent with another
    r = client.post("/webhooks/github", content=tampered, headers={
        "Content-Type": "application/json", "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "d-2001", "X-Hub-Signature-256": sig,
    })

    assert r.status_code == 401
    assert stream_records(redis_client) == [] and agent_api.requests == []


def test_missing_signature_is_401(client, agent_api, redis_client):
    r = gh_post(client, "issues", fixture_payload("issues.opened.json"), "d-2002", signature=None)
    assert r.status_code == 401
    assert stream_records(redis_client) == [] and agent_api.requests == []


def test_wrong_secret_is_401(client):
    r = gh_post(client, "issues", fixture_payload("issues.opened.json"), "d-2003", secret="wrong")
    assert r.status_code == 401


def test_non_allowlisted_event_is_204(client, agent_api, redis_client):
    r = gh_post(client, "push", {"repository": {"full_name": "vexa-ai/vexa"}}, "d-2004")
    assert r.status_code == 204
    assert stream_records(redis_client) == [] and agent_api.requests == []


def test_unmapped_action_is_204(client, agent_api, redis_client):
    payload = fixture_payload("issues.opened.json") | {"action": "labeled"}
    r = gh_post(client, "issues", payload, "d-2005")
    assert r.status_code == 204
    assert stream_records(redis_client) == [] and agent_api.requests == []


def test_ping_answers_pong(client, agent_api, redis_client):
    r = gh_post(client, "ping", {"zen": "Keep it logically awesome."}, "d-2006")
    assert r.status_code == 200 and r.json() == {"status": "pong"}
    assert stream_records(redis_client) == []


# ── idempotency ────────────────────────────────────────────────────────────────────────────────
def test_duplicate_delivery_id_is_idempotent(client, agent_api, redis_client):
    subscribe(redis_client, event="vcs.issue.opened")
    payload = fixture_payload("issues.opened.json")

    first = gh_post(client, "issues", payload, "d-3001")
    second = gh_post(client, "issues", payload, "d-3001")

    assert first.status_code == 202
    assert second.status_code == 200 and second.json()["status"] == "duplicate"
    assert len(stream_records(redis_client)) == 1      # persisted once
    assert len(agent_api.requests) == 1                # dispatched once


# ── persist-first + replay ─────────────────────────────────────────────────────────────────────
def test_no_subscription_persists_but_does_not_dispatch(client, agent_api, redis_client):
    r = gh_post(client, "issues", fixture_payload("issues.opened.json"), "d-4001")

    assert r.status_code == 202
    assert r.json()["subscriptions"] == 0 and r.json()["dispatched"] == 0
    assert len(stream_records(redis_client)) == 1      # the record is NOT lost
    assert agent_api.requests == []
    assert client.app.state.counters["no_subscription"] == 1


def test_dispatch_failure_never_loses_the_record(client, agent_api, redis_client):
    subscribe(redis_client, event="vcs.issue.opened")
    agent_api.status = 500                              # agent-api is down

    r = gh_post(client, "issues", fixture_payload("issues.opened.json"), "d-4002")

    assert r.status_code == 202                         # the webhook is still acked…
    assert r.json()["dispatched"] == 0
    assert len(stream_records(redis_client)) == 1       # …because the record landed FIRST
    assert client.app.state.counters["dispatch_failed"] == 1

    # Recovery: replay re-runs dispatch from the stored record once agent-api is back.
    agent_api.status = 202
    replayed = client.post("/internal/replay/d-4002")
    assert replayed.status_code == 200
    assert replayed.json()["dispatched"] == 1
    envelope = agent_api.bodies()[-1]
    validate_event(envelope)
    assert envelope["source"]["uri"] == "github://vexa-ai/vexa/issues/512"


def test_replay_uses_current_subscriptions(client, agent_api, redis_client):
    """A delivery that had no subscriber replays against the CURRENT vcs:subs view."""
    gh_post(client, "issues", fixture_payload("issues.opened.json"), "d-4003")
    assert agent_api.requests == []

    subscribe(redis_client, event="vcs.issue.opened", subject="u_late")
    replayed = client.post("/internal/replay/d-4003")

    assert replayed.json() == {
        "delivery_id": "d-4003", "event": "vcs.issue.opened", "subscriptions": 1, "dispatched": 1,
    }
    assert agent_api.bodies()[0]["subject"] == "u_late"


def test_replay_unknown_delivery_is_404(client):
    assert client.post("/internal/replay/never-seen").status_code == 404


# ── configuration fail-closed ──────────────────────────────────────────────────────────────────
def test_missing_secret_fails_closed_503(agent_api, redis_client):
    import httpx
    from fastapi.testclient import TestClient

    from vcs_ingress import create_app

    app = create_app("http://agent-api.test", webhook_secret="",
                     transport=httpx.MockTransport(agent_api.handler), redis_client=redis_client)
    unsigned = TestClient(app)
    r = gh_post(unsigned, "issues", fixture_payload("issues.opened.json"), "d-5001")
    assert r.status_code == 503
    assert stream_records(redis_client) == []
