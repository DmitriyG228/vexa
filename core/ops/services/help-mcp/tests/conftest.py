"""Shared eval fixtures — the SHIPPED app in-process, every effect port faked/injected.

All autonomous (the repo idiom): no docker, no network. ``create_app`` takes every port
injected — GitHub + agent-api behind ``httpx.MockTransport`` (each recording every hop),
redis as ``fakeredis`` (wrapped to capture XADD kwargs), the docs corpus as a tmp fixture,
and the TTL-cache clock as a settable fake. Contract conformance is checked BY PATH against
the sealed ``event.v1`` schema (the schema-not-import idiom): this package never imports
agent code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

import fakeredis
import httpx
import jsonschema
import pytest
from fastapi.testclient import TestClient
from referencing import Registry, Resource

from help_mcp import create_app

AGENT_API_URL = "http://agent-api.test"
REPO = "Vexa-ai/vexa"


# ── schema-by-path (P4): the sealed event.v1 is read from the monorepo tree, never imported ──
def _contracts_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "agent/contracts/event.v1/event.schema.json").exists():
            return parent
    raise FileNotFoundError("monorepo core root (holding agent/contracts/event.v1) not found")


@lru_cache(maxsize=None)
def _validator(rel: str, shape: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((_contracts_root() / rel).read_text())
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    return jsonschema.Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{shape}"}, registry=registry
    )


def validate_event(envelope: dict) -> None:
    """The dispatched help.escalated envelope conforms to event.v1 Event (the agent-api seam)."""
    _validator("agent/contracts/event.v1/event.schema.json", "Event").validate(envelope)


def repo_docs_root() -> Path:
    """The REAL shipped docs corpus (offline — files in this repo) for the guide/selector tests."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs/docs/deployment.mdx").exists():
            return parent / "docs" / "docs"
    raise FileNotFoundError("monorepo root (holding docs/docs) not found")


# ── the tmp docs-corpus fixture (4 small mdx files, incl. a fenced-code decoy) ────────────────
CORPUS: Dict[str, str] = {
    "install.mdx": (
        '---\ntitle: "Install"\n---\n\n'
        "Vexa self-hosts on your own machines with Docker Compose.\n\n"
        "## Quick start (Docker Compose)\n\n"
        "Run `make all` from the repo root to bring the whole compose stack up.\n\n"
        "```bash\n"
        "# not a heading: this hash line lives inside a code fence\n"
        "make all\n"
        "```\n\n"
        "## Air-gapped\n\n"
        "Mirror the images into your own registry for an air-gapped install.\n"
    ),
    "auth.mdx": (
        '---\ntitle: "Authentication"\n---\n\n'
        "## API keys\n\n"
        "Every request carries X-API-Key. Create keys with the admin token.\n"
    ),
    "bots.mdx": (
        '---\ntitle: "Meeting bots"\n---\n\n'
        "## Send a bot\n\n"
        "POST /bots with the meeting url sends a transcription bot into the meeting.\n\n"
        "## Bot troubleshooting\n\n"
        "If the bot fails to join, check the runtime logs first.\n"
    ),
    "how-to/stream.mdx": (
        '---\ntitle: "Stream transcripts"\n---\n\n'
        "## WebSocket stream\n\n"
        "Subscribe to the live transcript stream over the gateway websocket.\n"
    ),
}


@pytest.fixture
def docs_root(tmp_path: Path) -> Path:
    for rel, text in CORPUS.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


# ── the fake GitHub (MockTransport) ───────────────────────────────────────────────────────────
ISSUES = [
    {
        "title": "Bot fails to join Teams meetings",
        "html_url": "https://github.com/Vexa-ai/vexa/issues/101",
        "labels": [{"name": "status: accepted"}, {"name": "type: bug"}, {"name": "area: bot"}],
        "updated_at": "2026-06-30T12:00:00Z",
    },
    {
        "title": "Recording upload stalls when MinIO restarts",
        "html_url": "https://github.com/Vexa-ai/vexa/issues/102",
        "labels": [{"name": "status: accepted"}, {"name": "type: bug"}, {"name": "area: recordings"}],
        "updated_at": "2026-06-29T09:00:00Z",
    },
    {  # a PR riding the issues endpoint — must be filtered out
        "title": "docs tweak",
        "html_url": "https://github.com/Vexa-ai/vexa/pull/103",
        "pull_request": {"url": "https://api.github.com/repos/Vexa-ai/vexa/pulls/103"},
        "labels": [{"name": "status: accepted"}, {"name": "type: bug"}],
        "updated_at": "2026-06-28T08:00:00Z",
    },
]


@dataclass
class FakeGitHub:
    """Records every hop; list replies come from `issues`, POSTs are captured in `created`."""

    requests: List[httpx.Request] = field(default_factory=list)
    issues: List[dict] = field(default_factory=lambda: [dict(i) for i in ISSUES])
    created: List[dict] = field(default_factory=list)
    status: int = 200  # set 403/429 to fake a rate limit

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/issues"):
            self.created.append(json.loads(request.content))
            return httpx.Response(
                201, json={"html_url": f"https://github.com/{REPO}/issues/999", "number": 999}
            )
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "API rate limit exceeded"})
        return httpx.Response(200, json=self.issues)


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub()


# ── the fake agent-api (the /events sink) ─────────────────────────────────────────────────────
@dataclass
class FakeAgentApi:
    """Records every dispatched envelope; status is settable to fake an outage."""

    requests: List[httpx.Request] = field(default_factory=list)
    status: int = 202

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"workload_id": "w1", "trigger": "event"})

    def bodies(self) -> List[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def agent_api() -> FakeAgentApi:
    return FakeAgentApi()


# ── redis: fakeredis wrapped to capture XADD kwargs; a broken twin for the degrade tests ─────
class RecordingRedis:
    def __init__(self, inner):
        self.inner = inner
        self.xadd_calls: List[tuple] = []

    def xadd(self, *args, **kwargs):
        self.xadd_calls.append((args, kwargs))
        return self.inner.xadd(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class BrokenRedis:
    """Every stream op raises — the redis-absent degrade path."""

    def xadd(self, *args, **kwargs):
        raise ConnectionError("redis down")

    def xrevrange(self, *args, **kwargs):
        raise ConnectionError("redis down")


@pytest.fixture
def redis_client() -> RecordingRedis:
    return RecordingRedis(fakeredis.FakeRedis(decode_responses=True))


# ── the settable TTL-cache clock ──────────────────────────────────────────────────────────────
class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# ── the shipped app, fully injected ───────────────────────────────────────────────────────────
@pytest.fixture
def app_factory(docs_root, gh, agent_api, redis_client, clock):
    def make(**overrides):
        kwargs = dict(
            docs_root=docs_root,
            agent_api_url=AGENT_API_URL,
            github_repo=REPO,
            github_token="",
            ops_subject="ops",
            redis_client=redis_client,
            github_transport=httpx.MockTransport(gh.handler),
            agent_transport=httpx.MockTransport(agent_api.handler),
            clock=clock,
        )
        kwargs.update(overrides)
        return create_app(**kwargs)

    return make


@pytest.fixture
def client(app_factory) -> TestClient:
    return TestClient(app_factory())
