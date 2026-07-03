"""L3 seam — the consumer: XREADGROUP → validate → policy → execute → report → ack.

Proves (fakeredis stream + group, MockTransport agent-api): the group is created idempotently and
owned here; a delivered report ACKs; an undeliverable report retries with backoff and does NOT
ack (the entry stays pending and the next cycle redelivers — at-least-once); a 409 from agent-api
(the proposal already settled server-side) ACKs — the status check is the idempotency; a
schema-violating or policy-violating proposal is reported FAILED with the mint function provably
never called; a malformed entry is poison — acked + counted, never a wedge.
"""
from __future__ import annotations

import json

import httpx
import pytest

from conftest import REPORT_TOKEN, make_proposal
from vcs_executor.consumer import APPROVED_STREAM, GROUP, Consumer, ResultReporter
from vcs_executor.github_app import GitHubAppError, InstallationToken


class Mint:
    """A recording mint seam — the assertion surface for 'no credential for refused proposals'."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, repo: str, level: str) -> InstallationToken:
        self.calls.append((repo, level))
        if self.fail:
            raise GitHubAppError("no App installation for the repo")
        return InstallationToken("ghs_test", repo=repo, level=level)


class Execute:
    """A recording execute seam returning a fixed happy result."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, proposal: dict, token: InstallationToken, **_kwargs) -> dict:
        self.calls.append(proposal)
        return {"result_url": "https://github.com/vexa-ai/vexa/issues/512#c1"}


@pytest.fixture
def sleeps() -> list[float]:
    return []


def build(redis_client, agent_api, sleeps, *, attempts: int = 2, mint: Mint | None = None,
          execute: Execute | None = None) -> tuple[Consumer, Mint, Execute]:
    reporter = ResultReporter(
        "http://agent-api.test", REPORT_TOKEN,
        transport=httpx.MockTransport(agent_api.handler),
        attempts=attempts, sleep=sleeps.append,
    )
    mint = mint or Mint()
    execute = execute or Execute()
    consumer = Consumer(redis_client, reporter, mint=mint, execute_action=execute)
    consumer.ensure_group()
    return consumer, mint, execute


def enqueue(redis_client, proposal: dict) -> str:
    """XADD the way agent-api's decide() does: {id, proposal-json}."""
    return redis_client.xadd(APPROVED_STREAM, {"id": proposal["id"], "proposal": json.dumps(proposal)})


def pending_count(redis_client) -> int:
    return redis_client.xpending(APPROVED_STREAM, GROUP)["pending"]


# ── the happy path ────────────────────────────────────────────────────────────────────────────

def test_executes_reports_and_acks(redis_client, agent_api, sleeps):
    consumer, mint, execute = build(redis_client, agent_api, sleeps)
    proposal = make_proposal("comment")
    enqueue(redis_client, proposal)

    assert consumer.run_once() == 1
    assert [p["id"] for p in execute.calls] == ["prop_7b20de55"]
    assert mint.calls == [("vexa-ai/vexa", "L2")]              # scoped to the target + the level
    # the report hit the ONE writer of proposal state with the executor's bearer secret
    assert [r.url.path for r in agent_api.requests] == ["/internal/proposals/prop_7b20de55/executed"]
    assert agent_api.requests[0].headers["Authorization"] == f"Bearer {REPORT_TOKEN}"
    assert agent_api.bodies() == [{"result_url": "https://github.com/vexa-ai/vexa/issues/512#c1"}]
    assert pending_count(redis_client) == 0                    # acked
    assert consumer.counters["executed"] == 1 and consumer.counters["acked"] == 1


def test_group_creation_is_idempotent(redis_client, agent_api, sleeps):
    consumer, _, _ = build(redis_client, agent_api, sleeps)
    consumer.ensure_group()                                    # second create → BUSYGROUP, swallowed
    consumer.ensure_group()
    assert redis_client.xinfo_groups(APPROVED_STREAM)[0]["name"] == GROUP


# ── at-least-once: report failure → no ack → redelivery; 409 → ack ────────────────────────────

def test_undeliverable_report_retries_backs_off_and_leaves_pending(redis_client, agent_api, sleeps):
    agent_api.statuses = [500, 500]                            # agent-api down for both attempts
    consumer, _, execute = build(redis_client, agent_api, sleeps, attempts=2)
    enqueue(redis_client, make_proposal("comment"))

    consumer.run_once()
    assert len(agent_api.requests) == 2                        # retried
    assert sleeps == [0.5]                                     # exponential backoff between tries
    assert pending_count(redis_client) == 1                    # NOT acked — stays on the PEL
    assert consumer.counters["unacked"] == 1

    # the next cycle drains the PEL first: the entry is REDELIVERED (at-least-once), the report
    # now lands (200) and the entry is acked. Server-side idempotency is the status check.
    agent_api.statuses = [500, 500, 200]
    consumer.run_once()
    assert len(execute.calls) == 2                             # re-executed — at-least-once
    assert pending_count(redis_client) == 0


def test_409_from_agent_api_acks_without_retry(redis_client, agent_api, sleeps):
    """409 = the proposal is not `approved` server-side — an already-settled redelivery. Acked."""
    agent_api.statuses = [409]
    consumer, _, _ = build(redis_client, agent_api, sleeps, attempts=3)
    enqueue(redis_client, make_proposal("comment"))
    consumer.run_once()
    assert len(agent_api.requests) == 1                        # no retry on a definitive answer
    assert sleeps == []
    assert pending_count(redis_client) == 0


def test_network_error_counts_as_undeliverable(redis_client, sleeps):
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    reporter = ResultReporter("http://agent-api.test", REPORT_TOKEN,
                              transport=httpx.MockTransport(refuse), attempts=2, sleep=sleeps.append)
    assert reporter.report("prop_1", {"result_url": "u"}) is False
    assert sleeps == [0.5]


# ── defense in depth: refused BEFORE any credential ───────────────────────────────────────────

def test_schema_violating_proposal_is_failed_and_never_credentialed(redis_client, agent_api, sleeps):
    consumer, mint, execute = build(redis_client, agent_api, sleeps)
    broken = make_proposal("comment")
    del broken["rationale"]                                    # proposal.v1 requires rationale
    enqueue(redis_client, broken)
    consumer.run_once()

    assert mint.calls == []                                    # NO credential was ever minted
    assert execute.calls == []
    body = agent_api.bodies()[0]
    assert "refused by the executor gate" in body["error"]
    assert pending_count(redis_client) == 0                    # reported + acked (marked failed)
    assert consumer.counters["failed"] == 1


def test_level_action_violation_is_failed_and_never_credentialed(redis_client, agent_api, sleeps):
    consumer, mint, execute = build(redis_client, agent_api, sleeps)
    violating = make_proposal("push_branch")
    violating["level"] = "L2"                                  # a mutation claiming the batch level
    enqueue(redis_client, violating)
    consumer.run_once()
    assert mint.calls == [] and execute.calls == []
    assert "refused by the executor gate" in agent_api.bodies()[0]["error"]


def test_unapproved_proposal_is_failed_and_never_credentialed(redis_client, agent_api, sleeps):
    consumer, mint, execute = build(redis_client, agent_api, sleeps)
    pending = make_proposal("comment", status="pending")
    del pending["decision"]
    enqueue(redis_client, pending)
    consumer.run_once()
    assert mint.calls == [] and execute.calls == []
    assert "not approved" in agent_api.bodies()[0]["error"]


def test_mint_failure_is_reported_failed(redis_client, agent_api, sleeps):
    consumer, mint, execute = build(redis_client, agent_api, sleeps, mint=Mint(fail=True))
    enqueue(redis_client, make_proposal("comment"))
    consumer.run_once()
    assert execute.calls == []                                 # no action without a credential
    assert "no App installation" in agent_api.bodies()[0]["error"]


# ── poison entries never wedge the stream ─────────────────────────────────────────────────────

def test_malformed_entry_is_acked_and_counted(redis_client, agent_api, sleeps):
    consumer, mint, _ = build(redis_client, agent_api, sleeps)
    redis_client.xadd(APPROVED_STREAM, {"proposal": "not json"})
    redis_client.xadd(APPROVED_STREAM, {"unexpected": "shape"})
    consumer.run_once()
    assert consumer.counters["poison"] == 2
    assert pending_count(redis_client) == 0                    # acked — no infinite redelivery
    assert agent_api.requests == [] and mint.calls == []       # nothing reportable, nothing minted
