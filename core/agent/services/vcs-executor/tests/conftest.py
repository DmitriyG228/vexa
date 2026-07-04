"""Shared eval fixtures — the SHIPPED executor in-process, every effect faked (the repo idiom).

No docker, no network: agent-api and GitHub sit behind ``httpx.MockTransport`` recorders, redis
is ``fakeredis``, the GitHub App key is a test-generated RSA keypair, and the L3 "remote" is a
local bare git repo. Conformance is checked BY PATH against the sealed proposal.v1 schema — the
schema-not-import idiom: this package never imports agent-api code.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import fakeredis
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from vcs_executor.github_app import GitHubApp, InstallationToken

AGENT_API_URL = "http://agent-api.test"
REPORT_TOKEN = "test-executor-result-token"
APP_ID = "31337"
INSTALLATION_ID = 42
GITHUB_TOKEN_VALUE = "ghs_SUPERSECRETinstallation42"


# ── a conformant, human-APPROVED proposal (mirrors core/agent/tests/test_proposals.py) ─────────

_PAYLOADS = {
    "comment": {"comment": "Duplicate of #498 — fixed in v0.11.2."},
    "label": {"labels": ["bug", "duplicate"]},
    "close": {"comment": "stale — no repro in two releases"},
    "open_issue": {"title": "Flaky reconnect", "body": "Seen 3× this week.", "labels": ["bug"]},
    "push_branch": {"branch": "fix/port", "base": "main",
                    "patches": [{"path": "docs/q.md", "diff": "@@ -1 +1 @@\n-8000\n+8100"}]},
    "open_pr": {"branch": "fix/port", "base": "main", "title": "docs: fix port", "body": "8000→8100",
                "patches": [{"path": "docs/q.md", "diff": "@@ -1 +1 @@\n-8000\n+8100"}]},
}
_LEVELS = {"comment": "L2", "label": "L2", "close": "L2", "open_issue": "L2",
           "push_branch": "L3", "open_pr": "L3"}


def make_proposal(action: str = "comment", *, pid: str = "prop_7b20de55", **over) -> dict:
    """An approved proposal for ``action`` — the shape agent-api XADDs to proposal:approved."""
    p = {
        "id": pid,
        "subject": "u_jane",
        "routine": {"id": "rt_triage", "name": "Issue triage", "declared_access": _LEVELS[action]},
        "origin": {"unit_id": "agent-u_jane-event-abc123", "trigger": "event"},
        "level": _LEVELS[action],
        "action": action,
        "target": {"provider": "github", "repo": "vexa-ai/vexa", "kind": "issue", "number": 512},
        "payload": dict(_PAYLOADS[action]),
        "rationale": "the eval says so",
        "status": "approved",
        "decision": {"by": "u_jane", "at": "2026-06-30T09:03:00Z", "note": "checked"},
        "created_at": "2026-06-30T07:45:00Z",
    }
    p.update(over)
    return p


# ── fakes: agent-api (the report sink) + GitHub REST ──────────────────────────────────────────

@dataclass
class FakeAgentApi:
    """Records every /internal/proposals/{id}/executed report; statuses is a settable script
    (one status per successive request — the retry/backoff evals) that repeats its last entry."""

    requests: List[httpx.Request] = field(default_factory=list)
    statuses: List[int] = field(default_factory=lambda: [200])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.statuses[min(len(self.requests) - 1, len(self.statuses) - 1)]
        return httpx.Response(status, json={"status": "executed" if status == 200 else "error"})

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@dataclass
class FakeGitHub:
    """The GitHub REST surface the executor touches: installation lookup, token mint, and the
    L2/PR endpoints. Records everything; the minted token is a fixed test value."""

    requests: List[httpx.Request] = field(default_factory=list)
    fail_with: Optional[int] = None  # force an API failure on the ACTION endpoints

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/installation"):
            return httpx.Response(200, json={"id": INSTALLATION_ID})
        if path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
            return httpx.Response(201, json={"token": GITHUB_TOKEN_VALUE,
                                             "expires_at": "2026-07-03T13:00:00Z"})
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"message": "nope"})
        if path.endswith("/comments"):
            return httpx.Response(201, json={"html_url": "https://github.com/vexa-ai/vexa/issues/512#c1"})
        if path.endswith("/labels"):
            return httpx.Response(200, json=[{"name": "bug"}])
        if path.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/vexa-ai/vexa/pull/900"})
        if path.endswith("/issues"):
            return httpx.Response(201, json={"html_url": "https://github.com/vexa-ai/vexa/issues/900"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"html_url": "https://github.com/vexa-ai/vexa/issues/512"})
        return httpx.Response(404, json={"message": "unmapped test route"})

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.content]


@pytest.fixture
def agent_api() -> FakeAgentApi:
    return FakeAgentApi()


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


# ── the GitHub App under a test RSA keypair ────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def rsa_keypair() -> tuple[str, str]:
    """(private_pem, public_pem) — generated once per session (keygen is the slow bit)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private_pem, public_pem


@pytest.fixture
def github_app(rsa_keypair, github: FakeGitHub) -> GitHubApp:
    return GitHubApp(
        APP_ID, rsa_keypair[0],
        api_base="https://github.test",
        transport=httpx.MockTransport(github.handler),
        clock=lambda: 1_780_000_000.0,
    )


@pytest.fixture
def token() -> InstallationToken:
    """A pre-minted token for driving actions directly (no App handshake in the arrange)."""
    return InstallationToken(GITHUB_TOKEN_VALUE, repo="vexa-ai/vexa", level="L3")


# ── local git remotes (the L3 flow's "GitHub", no network) ─────────────────────────────────────

def _run(cwd, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def bare_remote(tmp_path: Path) -> Path:
    """A bare local repo standing in for the target GitHub repo, seeded with one commit on main
    (docs/q.md carrying the line the golden diff patches)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(seed, "init", "-b", "main")
    _run(seed, "config", "user.name", "seed")
    _run(seed, "config", "user.email", "seed@example.com")
    (seed / "docs").mkdir()
    (seed / "docs" / "q.md").write_text("8000\n")
    _run(seed, "add", "-A")
    _run(seed, "commit", "-m", "seed")
    _run(seed, "push", str(remote), "main")
    return remote


def remote_log(remote: Path, ref: str, fmt: str) -> str:
    return _run(remote, "log", "-1", f"--format={fmt}", ref)
