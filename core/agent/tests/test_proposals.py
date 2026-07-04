"""Proposal-queue eval — the HUMAN GATE (proposal.v1) is structural, not advisory.

Proves: the level↔action binding holds at EMISSION (every action × declared_access combination —
an under-declared routine cannot propose above its ceiling, a mislabeled level is non-conformant
as data) and at DECISION (an L3 mutation never rides a batch approve); the worker-side sink
authenticates the per-dispatch token and pins its ``sub`` to the proposal's subject (no token / a
forged token / another subject's token buys nothing); every transition lands on the append-only
audit stream IN ORDER and approval feeds the executor stream; the status machine only moves
pending→(approved|rejected)→(executed|failed); and the committed goldens conform (P8).
"""
from __future__ import annotations

import json

import fakeredis
import pytest
from fastapi.testclient import TestClient
from jsonschema.exceptions import ValidationError

import contracts
from control_plane import proposals as proposals_mod
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.proposals import (
    APPROVED_STREAM,
    AUDIT_STREAM,
    InvalidTransition,
    ProposalViolation,
    RedisProposalStore,
    UnknownProposal,
    check_emission,
    level_for_action,
)
from shared.adapters import LocalIdentityMinter
from shared.config import load_settings
from tests.conftest import _repo_root
from tests.fakes import FakeProposalStore, FakeRuntime

ACTIONS = ["comment", "label", "close", "open_issue", "push_branch", "open_pr"]
_RANK = {"L1": 1, "L2": 2, "L3": 3}

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


def _proposal(action: str = "comment", declared: str = "L2", subject: str = "u_jane", **over) -> dict:
    """A conformant pending proposal for ``action``, emitted by a ``declared``-access routine."""
    p = {
        "subject": subject,
        "routine": {"id": "rt_triage", "name": "Issue triage", "declared_access": declared},
        "origin": {"unit_id": "agent-u_jane-event-abc123", "trigger": "event"},
        "level": level_for_action(action),
        "action": action,
        "target": {"provider": "github", "repo": "vexa-ai/vexa", "kind": "issue", "number": 512},
        "payload": _PAYLOADS[action],
        "rationale": "the eval says so",
        "status": "pending",
    }
    p.update(over)
    return p


def _store() -> RedisProposalStore:
    return RedisProposalStore(fakeredis.FakeRedis(decode_responses=True))


# ── L1 contract: the committed goldens ARE the spec (P8) ─────────────────────────────────────────

@pytest.mark.parametrize("name", ["Proposal.pending-comment.json", "Proposal.approved-pr.json"])
def test_proposal_goldens_conform(name):
    golden = json.loads((_repo_root() / "agent/contracts/proposal.v1/golden" / name).read_text())
    contracts.validate_proposal(golden)


def test_decision_golden_conforms():
    golden = json.loads(
        (_repo_root() / "agent/contracts/proposal.v1/golden/Decision.batch-approve.json").read_text())
    contracts.validate_proposal_decision(golden)


def test_schema_binds_level_to_action():
    """The binding is IN THE DATA CONTRACT: an L2 envelope carrying a mutating action is
    non-conformant before any store logic runs."""
    mislabeled = _proposal("open_pr", declared="L3")
    mislabeled["level"] = "L2"  # claim the batch-approvable level for a mutation
    with pytest.raises(ValidationError):
        contracts.validate_proposal(mislabeled)


# ── emission: the full action × declared_access matrix (the structural gate at put) ──────────────

@pytest.mark.parametrize("declared", ["L1", "L2", "L3"])
@pytest.mark.parametrize("action", ACTIONS)
def test_emission_matrix_action_x_declared_access(action, declared):
    store = _store()
    proposal = _proposal(action=action, declared=declared)
    if _RANK[declared] >= _RANK[level_for_action(action)]:
        pid = store.put(proposal)
        assert store.get(pid)["status"] == "pending"
    else:
        with pytest.raises(ProposalViolation):
            store.put(proposal)
        assert store.list("u_jane") == []  # nothing leaks past the gate


def test_emission_without_declared_access_is_fail_closed():
    store = _store()
    anonymous = _proposal("comment")
    del anonymous["routine"]  # routine is schema-optional — but the gate refuses an anonymous emitter
    with pytest.raises(ProposalViolation):
        store.put(anonymous)


def test_emission_of_a_non_pending_status_is_refused():
    # A worker cannot emit a pre-approved proposal — the gate decides, never the emitter.
    with pytest.raises(ProposalViolation):
        check_emission(_proposal("comment", status="approved"))


def test_check_emission_rejects_mislabeled_level_even_without_the_schema():
    # Defense in depth: the code-side binding holds on its own (schema bypassed deliberately).
    with pytest.raises(ProposalViolation):
        check_emission({**_proposal("open_pr", declared="L3"), "level": "L2"})


def test_put_assigns_id_and_created_at_when_absent():
    store = _store()
    p = _proposal("comment")
    del p["status"]
    pid = store.put(p)
    stored = store.get(pid)
    assert pid.startswith("prop_") and stored["created_at"] and stored["status"] == "pending"


# ── decision: an L3 never rides a batch (the structural gate at decide) ──────────────────────────

def test_batch_decide_with_an_l3_id_is_refused():
    store = _store()
    l2 = store.put(_proposal("comment", declared="L3"))
    l3 = store.put(_proposal("open_pr", declared="L3"))
    with pytest.raises(ProposalViolation):
        store.decide([l2, l3], True, by="u_jane")
    # all-or-nothing: the L2 must NOT have been decided by the failed batch
    assert store.get(l2)["status"] == "pending" and store.get(l3)["status"] == "pending"


def test_l3_decides_alone_and_l2_batches():
    store = _store()
    l3 = store.put(_proposal("push_branch", declared="L3"))
    assert store.decide([l3], True, by="u_jane")[0]["status"] == "approved"  # per-action path
    a = store.put(_proposal("comment"))
    b = store.put(_proposal("label"))
    decided = store.decide([a, b], True, by="u_jane", note="batch ok")
    assert [p["status"] for p in decided] == ["approved", "approved"]
    assert decided[0]["decision"] == {"by": "u_jane", "at": decided[0]["decision"]["at"], "note": "batch ok"}


# ── status machine: pending→(approved|rejected)→(executed|failed), nothing else ──────────────────

def test_status_transitions_are_enforced():
    store = _store()
    pid = store.put(_proposal("comment"))
    with pytest.raises(InvalidTransition):
        store.mark_executed(pid, {"result_url": "u"})       # pending → executed is not a move
    store.decide([pid], False, by="u_jane")                  # pending → rejected
    with pytest.raises(InvalidTransition):
        store.decide([pid], True, by="u_jane")               # rejected is terminal for the gate
    with pytest.raises(InvalidTransition):
        store.mark_executed(pid, {"result_url": "u"})        # rejected never executes
    with pytest.raises(UnknownProposal):
        store.decide(["prop_deadbeef"], True, by="u_jane")


def test_mark_executed_stamps_execution_and_failure():
    store = _store()
    ok = store.put(_proposal("comment"))
    store.decide([ok], True, by="u_jane")
    done = store.mark_executed(ok, {"result_url": "https://github.com/vexa-ai/vexa/issues/512#c1"})
    assert done["status"] == "executed" and done["execution"]["result_url"].endswith("#c1")
    bad = store.put(_proposal("label"))
    store.decide([bad], True, by="u_jane")
    failed = store.mark_executed(bad, {"error": "404 from github"})
    assert failed["status"] == "failed" and failed["execution"]["error"] == "404 from github"


# ── audit: every transition lands on the append-only stream, in order ─────────────────────────────

def test_audit_stream_orders_every_transition_and_feeds_the_executor():
    r = fakeredis.FakeRedis(decode_responses=True)
    store = RedisProposalStore(r)
    pid = store.put(_proposal("comment"))
    store.decide([pid], True, by="u_jane")
    store.mark_executed(pid, {"result_url": "u"})
    transitions = [(f["from"], f["to"]) for _id, f in r.xrange(AUDIT_STREAM)]
    assert transitions == [("-", "pending"), ("pending", "approved"), ("approved", "executed")]
    ids = [f["id"] for _id, f in r.xrange(AUDIT_STREAM)]
    assert ids == [pid] * 3
    # approval XADDs the FULL proposal to the executor feed — the only thing the token-holder obeys
    approved = [json.loads(f["proposal"]) for _id, f in r.xrange(APPROVED_STREAM)]
    assert [p["id"] for p in approved] == [pid] and approved[0]["status"] == "approved"


def test_rejection_never_reaches_the_executor_feed():
    r = fakeredis.FakeRedis(decode_responses=True)
    store = RedisProposalStore(r)
    pid = store.put(_proposal("comment"))
    store.decide([pid], False, by="u_jane")
    assert r.xrange(APPROVED_STREAM) == []
    assert [(f["from"], f["to"]) for _id, f in r.xrange(AUDIT_STREAM)] == [("-", "pending"), ("pending", "rejected")]


# ── the HTTP surface: queue view, batch vs per-action, the token-verified sink ────────────────────

_KEY = "test-dispatch-signing-key"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _mint(subject: str = "u_jane") -> str:
    return LocalIdentityMinter(_KEY).mint(subject, f"user:{subject}", [{"id": subject, "mode": "ro"}], [])


def _client(store=None):
    store = store if store is not None else FakeProposalStore()
    app = create_app(
        Dispatcher(load_settings(), FakeRuntime(), _FakeIdentity()),
        proposals=store, token_verifier=LocalIdentityMinter(_KEY),
    )
    return TestClient(app), store


def test_queue_groups_by_routine_and_level():
    client, store = _client()
    store.put(_proposal("comment"))
    store.put(_proposal("label"))
    store.put(_proposal("open_pr", declared="L3",
                        routine={"id": "rt_docs", "name": "Docs fixer", "declared_access": "L3"}))
    r = client.get("/api/proposals", params={"status": "pending"})
    assert r.status_code == 200
    groups = {(g["routine"].get("id"), g["level"]): g for g in r.json()["groups"]}
    assert set(groups) == {("rt_triage", "L2"), ("rt_docs", "L3")}
    assert len(groups[("rt_triage", "L2")]["proposals"]) == 2
    assert groups[("rt_triage", "L2")]["batch_approvable"] is True
    assert groups[("rt_docs", "L3")]["batch_approvable"] is False


def test_get_proposal_is_subject_partitioned():
    client, store = _client()
    mine = store.put(_proposal("comment"))
    theirs = store.put(_proposal("comment", subject="u_bob"))
    assert client.get(f"/api/proposals/{mine}").status_code == 200          # conftest subject = u_jane
    assert client.get(f"/api/proposals/{theirs}").status_code == 404        # not 403 — no existence leak
    assert client.get("/api/proposals/prop_deadbeef").status_code == 404


def test_batch_decide_approves_l2_and_refuses_any_l3():
    client, store = _client()
    a = store.put(_proposal("comment"))
    b = store.put(_proposal("label"))
    l3 = store.put(_proposal("open_pr", declared="L3"))

    r = client.post("/api/proposals/decide", json={"ids": [a, b], "approve": True, "note": "lgtm"})
    assert r.status_code == 200
    assert all(p["status"] == "approved" for p in r.json()["decided"])

    # ANY L3 id on the batch surface — even alone — is a 403: L3 is per-action only.
    r = client.post("/api/proposals/decide", json={"ids": [l3], "approve": True})
    assert r.status_code == 403
    assert store.get(l3)["status"] == "pending"
    # deciding the already-decided batch again → 409 (the status machine, over HTTP)
    assert client.post("/api/proposals/decide", json={"ids": [a], "approve": False}).status_code == 409
    assert client.post("/api/proposals/decide", json={"ids": [], "approve": True}).status_code == 400


def test_per_action_approve_and_reject():
    client, store = _client()
    l3 = store.put(_proposal("open_pr", declared="L3"))
    l2 = store.put(_proposal("comment"))
    r = client.post(f"/api/proposals/{l3}/approve", json={"note": "diff checked"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert store.approved and store.approved[0]["id"] == l3                 # the executor feed got it
    r = client.post(f"/api/proposals/{l2}/reject")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert client.post(f"/api/proposals/{l3}/approve").status_code == 409   # already approved
    assert client.post("/api/proposals/prop_deadbeef/approve").status_code == 404


def test_sink_requires_a_valid_dispatch_token():
    client, store = _client()
    proposal = _proposal("comment")
    # no token → 401
    assert client.post("/internal/proposals", json=proposal).status_code == 401
    # garbage token → 401
    r = client.post("/internal/proposals", json=proposal, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    # a token signed with the WRONG key (a forgery) → 401
    forged = LocalIdentityMinter("attacker-key").mint("u_jane", "user:u_jane", [{"id": "u_jane", "mode": "ro"}], [])
    r = client.post("/internal/proposals", json=proposal, headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401
    # a VALID token for ANOTHER subject → 403 (a worker proposes only as who it was dispatched for)
    r = client.post("/internal/proposals", json=proposal, headers={"Authorization": f"Bearer {_mint('u_bob')}"})
    assert r.status_code == 403
    assert store.proposals == {}  # nothing got in

    # the real path: the dispatched subject's token → 201 + queued pending
    r = client.post("/internal/proposals", json=proposal, headers={"Authorization": f"Bearer {_mint()}"})
    assert r.status_code == 201
    assert store.get(r.json()["id"])["status"] == "pending"


def test_sink_enforces_the_emission_gate_and_the_contract():
    client, _store_ = _client()
    headers = {"Authorization": f"Bearer {_mint()}"}
    # an under-declared routine proposing a mutation → 403 (the structural gate, at the boundary)
    r = client.post("/internal/proposals", json=_proposal("open_pr", declared="L2"), headers=headers)
    assert r.status_code == 403
    # a non-conformant envelope → 400, fail loud (P18)
    r = client.post("/internal/proposals", json={"subject": "u_jane", "action": "comment"}, headers=headers)
    assert r.status_code == 400


def test_unwired_store_answers_501_honestly():
    app = create_app(Dispatcher(load_settings(), FakeRuntime(), _FakeIdentity()))
    client = TestClient(app)
    assert client.get("/api/proposals").status_code == 501
    assert client.post("/internal/proposals", json=_proposal()).status_code == 501


# ── the executor report-back sink (/internal/proposals/{id}/executed) ─────────────────────────────
# agent-api stays the ONE writer of proposal state (P23): the vcs-executor reports here, never
# touches redis. Auth is a dedicated STANDING-SERVICE shared secret (VEXA_EXECUTOR_RESULT_TOKEN,
# constant-time) — the per-dispatch verifier doesn't apply (nothing mints for a standing service).

_EXEC_TOKEN = "test-executor-result-token"


def _executor_client(store=None, *, token: str = _EXEC_TOKEN):
    store = store if store is not None else FakeProposalStore()
    app = create_app(
        Dispatcher(load_settings(executor_result_token=token), FakeRuntime(), _FakeIdentity()),
        proposals=store, token_verifier=LocalIdentityMinter(_KEY),
    )
    return TestClient(app), store


def _approved(store, action: str = "comment") -> str:
    pid = store.put(_proposal(action, declared="L3"))
    store.decide([pid], True, by="u_jane")
    return pid


def test_executed_sink_auth_matrix():
    client, store = _executor_client()
    pid = _approved(store)
    url = f"/internal/proposals/{pid}/executed"
    # no bearer → 401; wrong bearer → 401 (constant-time compare, no oracle)
    assert client.post(url, json={"result_url": "u"}).status_code == 401
    r = client.post(url, json={"result_url": "u"}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    # the DISPATCH token (the worker-side credential) buys nothing on this standing-service edge
    r = client.post(url, json={"result_url": "u"}, headers={"Authorization": f"Bearer {_mint()}"})
    assert r.status_code == 401
    assert store.get(pid)["status"] == "approved"            # nothing moved
    # the executor's own secret → 200
    r = client.post(url, json={"result_url": "u"}, headers={"Authorization": f"Bearer {_EXEC_TOKEN}"})
    assert r.status_code == 200 and r.json()["status"] == "executed"


def test_executed_sink_fails_closed_when_unconfigured():
    client, store = _executor_client(token="")
    pid = _approved(store)
    r = client.post(f"/internal/proposals/{pid}/executed", json={"result_url": "u"},
                    headers={"Authorization": "Bearer anything"})
    assert r.status_code == 503                              # unconfigured ⇒ refuse, never accept


def test_executed_sink_status_transitions_and_audit():
    r_ = fakeredis.FakeRedis(decode_responses=True)
    client, store = _executor_client(RedisProposalStore(r_))
    headers = {"Authorization": f"Bearer {_EXEC_TOKEN}"}
    ok = _approved(store)
    resp = client.post(f"/internal/proposals/{ok}/executed",
                       json={"result_url": "https://github.com/vexa-ai/vexa/pull/9", "sha": "a" * 40},
                       headers=headers)
    assert resp.status_code == 200
    assert resp.json()["execution"]["sha"] == "a" * 40 and resp.json()["execution"]["at"]
    # an error result maps to failed
    bad = _approved(store, "label")
    resp = client.post(f"/internal/proposals/{bad}/executed", json={"error": "422 from github"},
                       headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "failed"
    # the audit stream carries BOTH terminal transitions, stamped by the executor path
    transitions = [(f["from"], f["to"], f["by"]) for _id, f in r_.xrange(AUDIT_STREAM)]
    assert (("approved", "executed", "executor") in transitions
            and ("approved", "failed", "executor") in transitions)
    # idempotency contract: a second report for a settled proposal → 409 (the executor acks on it)
    resp = client.post(f"/internal/proposals/{ok}/executed", json={"result_url": "u"}, headers=headers)
    assert resp.status_code == 409
    # a still-pending proposal cannot be marked executed → 409; an unknown id → 404
    pending = store.put(_proposal("close"))
    assert client.post(f"/internal/proposals/{pending}/executed", json={"result_url": "u"},
                       headers=headers).status_code == 409
    assert client.post("/internal/proposals/prop_deadbeef/executed", json={"result_url": "u"},
                       headers=headers).status_code == 404


def test_executed_sink_refuses_extra_fields():
    client, store = _executor_client()
    pid = _approved(store)
    r = client.post(f"/internal/proposals/{pid}/executed",
                    json={"result_url": "u", "status": "executed"},   # status is server-owned
                    headers={"Authorization": f"Bearer {_EXEC_TOKEN}"})
    assert r.status_code == 422
    assert store.get(pid)["status"] == "approved"


# ── the worker emission tool (the stdio MCP server, offline) ──────────────────────────────────────

def test_tool_derives_level_and_dispatch_identity_from_env():
    from worker.tools.proposals_mcp import build_proposal

    env = {"VEXA_OWNER": "u_jane", "VEXA_UNIT_ID": "agent-u_jane-event-abc123", "VEXA_UNIT_TRIGGER": "event"}
    p = build_proposal({
        "action": "open_pr",
        "target": {"repo": "vexa-ai/vexa", "kind": "branch", "ref": "fix/port"},
        "payload": _PAYLOADS["open_pr"],
        "rationale": "docs drift",
        "routine": {"id": "rt_docs", "declared_access": "L3"},
    }, env)
    assert p["level"] == "L3"                                # DERIVED from the action, never model-set
    assert p["subject"] == "u_jane" and p["origin"]["unit_id"] == "agent-u_jane-event-abc123"
    assert p["target"]["provider"] == "github" and p["status"] == "pending"
    with pytest.raises(ValueError):
        build_proposal({"action": "force_push"}, env)        # an unknown action never leaves the worker


def test_tool_emission_reaches_the_sink_end_to_end(monkeypatch):
    """The whole worker→sink wire, offline: the MCP tools/call assembles the proposal, 'POSTs' it
    (httpx patched onto the TestClient), the sink verifies the token and queues it pending."""
    import httpx

    from worker.tools import proposals_mcp

    client, store = _client()
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, timeout=None:
                        client.post("/internal/proposals", json=json, headers=headers))
    env = {
        "VEXA_AGENT_API_URL": "http://agent-api:8100", "VEXA_AGENT_IDENTITY_TOKEN": _mint(),
        "VEXA_OWNER": "u_jane", "VEXA_UNIT_ID": "agent-u_jane-event-abc123", "VEXA_UNIT_TRIGGER": "event",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    resp = proposals_mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
        "name": "propose_vcs_action",
        "arguments": {
            "action": "comment",
            "target": {"repo": "vexa-ai/vexa", "kind": "issue", "number": 512},
            "payload": {"comment": "dup of #498"},
            "rationale": "same stack trace",
            "routine": {"id": "rt_triage", "declared_access": "L2"},
        },
    }})
    outcome = json.loads(resp["result"]["content"][0]["text"])
    assert resp["result"]["isError"] is False and outcome["ok"] is True
    assert store.get(outcome["id"])["action"] == "comment"
    # and the protocol shell answers the handshake
    init = proposals_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    tools = proposals_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert init["result"]["serverInfo"]["name"] == "vexa-proposals"
    assert [t["name"] for t in tools["result"]["tools"]] == ["propose_vcs_action"]


def test_tool_descriptor_is_conformant_and_credless():
    descriptor = json.loads(
        (_repo_root() / "agent/tools-seed/propose_vcs_action.json").read_text())
    contracts.validate_tool(descriptor["tool"])
    assert descriptor["tool"]["grant"] == "auto"        # emitting is free — the gate is at approval
    assert "cred_ref" not in descriptor["tool"]         # no secret enters the worker for this (P15)
