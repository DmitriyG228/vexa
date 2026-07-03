"""Shared eval fixtures — the SHIPPED app in-process, both effect ports faked.

All autonomous (the repo idiom): no docker, no network. ``create_app`` takes both ports injected —
agent-api behind ``httpx.MockTransport`` (records every dispatched envelope) and redis as a
``fakeredis`` instance (the dedupe keys, the vcs:events stream, the vcs:subs read view). Contract
conformance is checked BY PATH against the sealed schemas (ingress.v1 + event.v1) — the
schema-not-import idiom: this package never imports agent-api code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List

import fakeredis
import httpx
import jsonschema
import pytest
from fastapi.testclient import TestClient
from referencing import Registry, Resource

from vcs_ingress import create_app, signature_of

AGENT_API_URL = "http://agent-api.test"
SECRET = "test-webhook-secret"
FIXTURES = Path(__file__).parent / "fixtures"


# ── schema-by-path (P4): the sealed contracts are read from the monorepo tree, never imported ──
def _contracts_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "agent/contracts/ingress.v1/ingress.schema.json").exists():
            return parent
    raise FileNotFoundError("monorepo core root (holding agent/contracts/ingress.v1) not found")


@lru_cache(maxsize=None)
def _validator(rel: str, shape: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((_contracts_root() / rel).read_text())
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    return jsonschema.Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{shape}"}, registry=registry
    )


def validate_delivery(record: dict) -> None:
    """The persisted record conforms to ingress.v1 Delivery."""
    _validator("agent/contracts/ingress.v1/ingress.schema.json", "Delivery").validate(record)


def validate_event(envelope: dict) -> None:
    """The dispatched envelope conforms to event.v1 Event (the agent-api seam)."""
    _validator("agent/contracts/event.v1/event.schema.json", "Event").validate(envelope)


def fixture_payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ── the fake agent-api ─────────────────────────────────────────────────────────────────────────
@dataclass
class FakeAgentApi:
    """Records every dispatch hop the ingress makes; status is settable to fake an outage."""

    requests: List[httpx.Request] = field(default_factory=list)
    status: int = 202

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"workload_id": "w1", "trigger": "event"})

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def agent_api() -> FakeAgentApi:
    return FakeAgentApi()


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def client(agent_api: FakeAgentApi, redis_client) -> TestClient:
    app = create_app(
        AGENT_API_URL,
        webhook_secret=SECRET,
        transport=httpx.MockTransport(agent_api.handler),
        redis_client=redis_client,
    )
    return TestClient(app)


# ── helpers ────────────────────────────────────────────────────────────────────────────────────
def gh_post(
    client: TestClient,
    event: str,
    payload: dict,
    delivery_id: str = "d-0001",
    *,
    secret: str = SECRET,
    signature: str | None = ...,  # ... = compute a VALID signature; None = omit the header
):
    """POST a webhook the way GitHub does: raw JSON body + event/delivery/signature headers."""
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
    }
    if signature is ...:
        headers["X-Hub-Signature-256"] = signature_of(secret, body)
    elif signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhooks/github", content=body, headers=headers)


def subscribe(
    redis_client,
    *,
    repo: str = "vexa-ai/vexa",
    event: str = "vcs.issue.opened",
    subject: str = "u_jane",
    prompt: str = "Fetch the item at the event ref with your read-only vcs tool and triage it.",
    access: str = "L1",
    routine_id: str = "rt_test0001",
) -> dict:
    """Write a vcs:subs record the way agent-api's reconciler does (the tests' arrange step —
    in production agent-api is the ONLY writer of this hash)."""
    record = {
        "routine_id": routine_id,
        "subject": subject,
        "name": "test-routine",
        "event": event,
        "repo": repo,
        "access": access,
        "plan": {"prompt": prompt},
    }
    existing = json.loads(redis_client.hget("vcs:subs", repo) or "[]")
    existing.append(record)
    redis_client.hset("vcs:subs", repo, json.dumps(existing))
    return record


def stream_records(redis_client) -> list[dict]:
    """Every Delivery persisted on vcs:events, parsed."""
    return [json.loads(fields["record"]) for _id, fields in redis_client.xrange("vcs:events")]
