"""proposals.py — the proposal queue (the HUMAN GATE for external VCS actions, ``proposal.v1``).

Agent routines never touch GitHub: a unit EMITS a Proposal (the worker's ``propose_vcs_action``
tool → ``POST /internal/proposals``), a HUMAN decides it, and a separate credentialed EXECUTOR
(future PR) consumes the approved feed. The credential and the gate sit on OPPOSITE sides of the
agent (P15): the worker container holds no GitHub token, and the token-holder executes nothing a
human didn't approve — a prompt-injected unit can at worst *ask*.

Levels are STRUCTURALLY bound to actions (a table in code + the schema's ``allOf`` — never data):

    L2 — annotate: comment | label | close | open_issue   (batch-approvable)
    L3 — mutate:   push_branch | open_pr                  (per-action approval ONLY)

Enforced at BOTH ends of the queue. ``put`` (emission) rejects an action outside its declared
level, a routine whose ``declared_access`` is below the action's level, a proposal with NO declared
access (fail-closed), and any non-``pending`` status (the gate decides — never the emitter).
``decide`` (decision) refuses a multi-proposal batch carrying an L3 id, so a mutation can never
ride a bulk approve — each L3 is one deliberate human act.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol

import contracts

# ── the structural level ↔ action binding (mirrors proposal.v1's allOf — code, not config) ────────
LEVEL_ACTIONS: dict[str, frozenset[str]] = {
    "L2": frozenset({"comment", "label", "close", "open_issue"}),
    "L3": frozenset({"push_branch", "open_pr"}),
}
_ACTION_LEVEL = {action: level for level, actions in LEVEL_ACTIONS.items() for action in actions}
# L1 = read-only (may not propose at all); the rank order is what "declared_access below the
# action's level" means.
_ACCESS_RANK = {"L1": 1, "L2": 2, "L3": 3}

AUDIT_STREAM = "proposal:audit"        # append-only: EVERY status transition is XADDed here
APPROVED_STREAM = "proposal:approved"  # the (future) executor's consumer-group feed


class ProposalViolation(PermissionError):
    """A structural level/access violation — 403 semantics at the HTTP surface."""


class UnknownProposal(KeyError):
    """No proposal with that id — 404 semantics."""


class InvalidTransition(ValueError):
    """The proposal isn't in the status this operation requires — 409 semantics."""


def level_for_action(action: str) -> str:
    """The level an action structurally belongs to. Raises ``ValueError`` on an unknown action."""
    try:
        return _ACTION_LEVEL[action]
    except KeyError:
        raise ValueError(f"unknown proposal action {action!r}")


def check_emission(proposal: dict) -> None:
    """The emission-side structural guard (``put``). Assumes a contract-conformant envelope.

    Fail-closed on: an action outside its claimed level; a missing/unknown ``routine.declared_access``
    (an anonymous emitter gets nothing); a declared access below the action's level; and any status
    other than ``pending`` (a worker cannot emit a pre-approved proposal).
    """
    level, action = proposal["level"], proposal["action"]
    if action not in LEVEL_ACTIONS.get(level, frozenset()):
        raise ProposalViolation(f"action {action!r} is not an {level} action (it is {level_for_action(action)})")
    declared = (proposal.get("routine") or {}).get("declared_access")
    if declared not in _ACCESS_RANK:
        raise ProposalViolation("proposal carries no routine.declared_access — emission is fail-closed")
    if _ACCESS_RANK[declared] < _ACCESS_RANK[level]:
        raise ProposalViolation(
            f"routine {(proposal.get('routine') or {}).get('id', '?')!r} is declared {declared} — "
            f"it may not emit an {level} action ({action!r})"
        )
    if proposal.get("status") != "pending":
        raise ProposalViolation("a proposal is emitted 'pending' — the gate decides, never the emitter")


def check_batch(proposals: list[dict]) -> None:
    """The decision-side structural guard (``decide``): an L3 may only be decided ALONE.

    A multi-proposal batch containing any L3 id is refused — a mutation never rides a bulk approve.
    (The HTTP batch surface is stricter still: it refuses L3 entirely; see api.py.)
    """
    if len(proposals) > 1:
        l3 = [p["id"] for p in proposals if p.get("level") == "L3"]
        if l3:
            raise ProposalViolation(
                f"L3 proposal(s) {', '.join(l3)} cannot ride a batch decision — approve each per-action"
            )


class TokenVerifier(Protocol):
    """The boundary-verification seam the emission sink needs: verify a per-dispatch identity token
    and return its claims (``LocalIdentityMinter.verify`` is the dev-tier adapter)."""

    def verify(self, token: str) -> dict: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RedisProposalStore:
    """``ProposalStorePort`` over redis — hash per proposal, per-subject indexes, append-only audit.

    Keys (mirrors the ``_Sessions`` idiom in api.py — hashes + per-subject id sets):
      ``proposal:<id>``              — hash {json, status, subject} (the document + fast filters)
      ``proposal:pending:<subject>`` — set of the subject's undecided ids (the queue view)
      ``proposal:ids:<subject>``     — set of ALL the subject's ids (list() over any status)
      ``proposal:audit``             — append-only stream: every transition XADDed {id, from, to, by, at}
      ``proposal:approved``          — approval feed: the FULL proposal, XADDed for the (future)
                                       executor to XREADGROUP — the only thing the token-holder obeys
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    # ── redis key helpers ──
    @staticmethod
    def _key(proposal_id: str) -> str:
        return f"proposal:{proposal_id}"

    @staticmethod
    def _pending_key(subject: str) -> str:
        return f"proposal:pending:{subject}"

    @staticmethod
    def _ids_key(subject: str) -> str:
        return f"proposal:ids:{subject}"

    def _audit(self, proposal_id: str, from_status: str, to_status: str, *, by: str) -> None:
        self._redis.xadd(AUDIT_STREAM, {
            "id": proposal_id, "from": from_status or "-", "to": to_status, "by": by or "-", "at": _now(),
        })

    def _save(self, proposal: dict) -> None:
        self._redis.hset(self._key(proposal["id"]), mapping={
            "json": json.dumps(proposal), "status": proposal["status"], "subject": proposal["subject"],
        })

    # ── the port ──
    def put(self, proposal: dict) -> str:
        """Validate (contract, P8) + structurally enforce (level/access, fail-closed), then store.

        The store OWNS id/created_at/status defaults so the emitting tool stays thin: a proposal
        arriving without them gets ``prop_<hex>`` / now / ``pending`` before validation."""
        p = dict(proposal)
        p.setdefault("id", f"prop_{uuid.uuid4().hex}")
        p.setdefault("status", "pending")
        p.setdefault("created_at", _now())
        contracts.validate_proposal(p)   # fail loud at the seam (P18)
        check_emission(p)                # the structural human-gate guard (emission side)
        if self._redis.exists(self._key(p["id"])):
            raise ValueError(f"proposal {p['id']} already exists")
        self._save(p)
        self._redis.sadd(self._ids_key(p["subject"]), p["id"])
        self._redis.sadd(self._pending_key(p["subject"]), p["id"])
        self._audit(p["id"], "", "pending", by=(p.get("origin") or {}).get("unit_id", ""))
        return p["id"]

    def get(self, proposal_id: str) -> Optional[dict]:
        raw = self._redis.hget(self._key(proposal_id), "json")
        return json.loads(raw) if raw else None

    def list(self, subject: str, status: Optional[str] = None) -> list[dict]:
        key = self._pending_key(subject) if status == "pending" else self._ids_key(subject)
        rows: list[dict] = []
        for pid in self._redis.smembers(key) or set():
            p = self.get(pid)
            if p is not None and (status is None or p["status"] == status):
                rows.append(p)
        rows.sort(key=lambda p: (p.get("created_at", ""), p["id"]))
        return rows

    def decide(self, ids: list[str], approve: bool, by: str, note: str = "") -> list[dict]:
        """One human decision over pending proposals — all-or-nothing (validate every id BEFORE
        mutating any). Approval additionally XADDs the full proposal to ``proposal:approved``."""
        if not ids:
            raise ValueError("decision carries no ids")
        proposals: list[dict] = []
        for pid in dict.fromkeys(ids):  # de-dup, order-preserving
            p = self.get(pid)
            if p is None:
                raise UnknownProposal(pid)
            proposals.append(p)
        check_batch(proposals)           # the structural human-gate guard (decision side)
        for p in proposals:
            if p["status"] != "pending":
                raise InvalidTransition(f"proposal {p['id']} is {p['status']!r}, not pending")
        to_status = "approved" if approve else "rejected"
        decided_at = _now()
        updated: list[dict] = []
        for p in proposals:
            p["status"] = to_status
            p["decision"] = {"by": by, "at": decided_at, **({"note": note} if note else {})}
            self._save(p)
            self._redis.srem(self._pending_key(p["subject"]), p["id"])
            self._audit(p["id"], "pending", to_status, by=by)
            if approve:
                self._redis.xadd(APPROVED_STREAM, {"id": p["id"], "proposal": json.dumps(p)})
            updated.append(p)
        return updated

    def mark_executed(self, proposal_id: str, result: dict) -> Optional[dict]:
        p = self.get(proposal_id)
        if p is None:
            raise UnknownProposal(proposal_id)
        if p["status"] != "approved":
            raise InvalidTransition(f"proposal {proposal_id} is {p['status']!r}, not approved")
        execution = dict(result or {})
        execution.setdefault("at", _now())
        to_status = "failed" if execution.get("error") else "executed"
        p["status"] = to_status
        p["execution"] = execution
        self._save(p)
        self._audit(p["id"], "approved", to_status, by="executor")
        return p
