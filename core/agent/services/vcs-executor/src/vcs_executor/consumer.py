"""consumer.py — XREADGROUP on ``proposal:approved`` → validate → policy → execute → report.

The executor OWNS the consumer group (created idempotently at start — agent-api only XADDs the
feed). Per entry, in order, all BEFORE any credential is minted:

1. the full proposal (XADDed by agent-api at approval) is re-validated against the sealed
   ``proposal.v1`` schema BY PATH (P8 — the stream is a transport, the contract is the truth);
2. the policy gate re-checks the level↔action binding, the approved+decision status, and the L3
   branch policy (defense in depth) — a violating proposal is reported **failed**, never executed
   and never credentialed;
3. only then is the per-proposal installation token minted and the action executed.

**Result reporting** keeps agent-api the single writer of proposal state (P23): the outcome goes
to ``POST /internal/proposals/{id}/executed`` (shared-secret bearer). Semantics are AT-LEAST-ONCE:

* report delivered (200) → XACK;
* agent-api answers 409 (the proposal is not ``approved`` — a redelivery of an already-settled
  entry) → XACK, the server-side status check IS the idempotency;
* the report cannot be delivered (network / 5xx / anything else) → retry with backoff, and on
  exhaustion do NOT ack — the entry stays on the PEL and the next cycle redelivers it.

``run_once`` drains the consumer's own pending-entries list (``id "0"`` — redelivery after a
crash or a failed report) before reading new entries (``id ">"``).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import httpx
import jsonschema
from referencing import Registry, Resource

from . import policy
from .actions import ExecutionError, execute
from .github_app import GitHubAppError, InstallationToken
from .policy import PolicyViolation

log = logging.getLogger(__name__)

APPROVED_STREAM = "proposal:approved"   # written by agent-api at approval; this service only reads
GROUP = "vcs-executor"                  # the consumer group THIS service creates and owns


# ── proposal.v1 by schema path (P4/P8 — the schema-not-import idiom) ──────────────────────────

def _contracts_root() -> Path:
    """Walk up to the tree holding ``agent/contracts/proposal.v1`` (the monorepo ``core/`` root
    in dev, ``/app`` in the image where the Dockerfile vendors the sealed schema)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "agent/contracts/proposal.v1/proposal.schema.json").exists():
            return parent
    raise FileNotFoundError("agent/contracts/proposal.v1/proposal.schema.json not found on any parent")


@lru_cache(maxsize=1)
def _proposal_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(
        (_contracts_root() / "agent/contracts/proposal.v1/proposal.schema.json").read_text()
    )
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    return jsonschema.Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/Proposal"}, registry=registry
    )


def validate_proposal(proposal: dict) -> None:
    """Raises ``jsonschema.exceptions.ValidationError`` on a non-conformant proposal."""
    _proposal_validator().validate(proposal)


# ── the report-back edge (agent-api stays the one writer of proposal state) ───────────────────

class ResultReporter:
    """POST the execution outcome to agent-api's ``/internal/proposals/{id}/executed`` sink.

    ``report`` returns True when the entry may be ACKed (delivered, or 409 = already settled
    server-side) and False when it must stay pending (report undeliverable after retries).
    """

    def __init__(
        self,
        agent_api_url: str,
        token: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        attempts: int = 5,
        backoff_base_sec: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = agent_api_url.rstrip("/")
        self._token = token
        self._transport = transport
        self._attempts = max(1, attempts)
        self._backoff = backoff_base_sec
        self._sleep = sleep

    def report(self, proposal_id: str, result: dict) -> bool:
        url = f"{self._base}/internal/proposals/{proposal_id}/executed"
        headers = {"Authorization": f"Bearer {self._token}"}
        for attempt in range(self._attempts):
            try:
                with httpx.Client(timeout=10, transport=self._transport) as client:
                    resp = client.post(url, json=result, headers=headers)
            except httpx.HTTPError as exc:
                log.warning("result report for %s failed (attempt %d): %s", proposal_id, attempt + 1, exc)
            else:
                if resp.status_code == 200:
                    return True
                if resp.status_code == 409:
                    # Not `approved` server-side — this entry was already settled (a redelivery).
                    # The status check IS the idempotency; ack and move on.
                    log.info("result report for %s answered 409 — already settled, acking", proposal_id)
                    return True
                log.warning(
                    "result report for %s answered %d (attempt %d)",
                    proposal_id, resp.status_code, attempt + 1,
                )
            if attempt < self._attempts - 1:
                self._sleep(self._backoff * (2 ** attempt))
        return False


# ── the consumer ──────────────────────────────────────────────────────────────────────────────

class Consumer:
    """The stream half: read → gate → execute → report → ack. All effects are injected:
    ``redis_client`` (fakeredis in tests), ``mint`` (the GitHubApp seam — provably NEVER called
    for a refused proposal), ``execute`` (the actions seam) and ``reporter``."""

    def __init__(
        self,
        redis_client,
        reporter: ResultReporter,
        *,
        mint: Callable[[str, str], InstallationToken],
        execute_action: Callable[..., dict] = execute,
        protected: tuple[str, ...] = policy.DEFAULT_PROTECTED,
        consumer_name: str = "executor-1",
        validate: Callable[[dict], None] = validate_proposal,
        **execute_kwargs,
    ) -> None:
        self._redis = redis_client
        self._reporter = reporter
        self._mint = mint
        self._execute = execute_action
        self._protected = protected
        self._name = consumer_name
        self._validate = validate
        self._execute_kwargs = execute_kwargs
        self.counters = {"executed": 0, "failed": 0, "acked": 0, "unacked": 0, "poison": 0}

    # the group is created idempotently — the executor OWNS it (agent-api only XADDs the stream)
    def ensure_group(self) -> None:
        try:
            self._redis.xgroup_create(APPROVED_STREAM, GROUP, id="0", mkstream=True)
        except Exception as exc:  # redis.ResponseError: BUSYGROUP — the group already exists
            if "BUSYGROUP" not in str(exc):
                raise

    def run_once(self, *, block_ms: Optional[int] = None, count: int = 10) -> int:
        """Drain own pending entries (redelivery), then read new ones. Returns entries handled."""
        handled = 0
        for read_id in ("0", ">"):
            resp = self._redis.xreadgroup(
                GROUP, self._name, {APPROVED_STREAM: read_id},
                count=count, block=block_ms if read_id == ">" else None,
            )
            for _stream, entries in resp or []:
                for entry_id, fields in entries:
                    if read_id == "0" and not fields:
                        continue  # PEL bookkeeping rows carry no fields
                    self._handle(entry_id, fields)
                    handled += 1
        return handled

    def run_forever(self, stop: threading.Event, *, block_ms: int = 5000) -> None:
        self.ensure_group()
        while not stop.is_set():
            try:
                self.run_once(block_ms=block_ms)
            except Exception:  # a poisoned cycle must not kill the service — log + continue (P18)
                log.exception("consumer cycle failed")
                stop.wait(1.0)

    # ── per entry ─────────────────────────────────────────────────────────────────────────────
    def _handle(self, entry_id: str, fields: dict) -> None:
        raw = fields.get("proposal") or ""
        try:
            proposal = json.loads(raw)
            proposal_id = proposal["id"]
        except (ValueError, TypeError, KeyError):
            # A malformed entry can never be reported (no id) — ack it so it cannot wedge the
            # stream, and count it loudly (P18: a visible state, never a silent drop).
            log.error("poison entry %s on %s: not a proposal — acked", entry_id, APPROVED_STREAM)
            self.counters["poison"] += 1
            self._redis.xack(APPROVED_STREAM, GROUP, entry_id)
            return

        result: dict
        try:
            self._validate(proposal)                        # proposal.v1, by schema path (P8)
            policy.check(proposal, protected=self._protected)  # the pre-credential gate
        except (jsonschema.exceptions.ValidationError, PolicyViolation) as exc:
            # Refused BEFORE any mint — marked failed, never executed (defense in depth).
            log.warning("proposal %s refused: %s", proposal_id, exc)
            result = {"error": f"refused by the executor gate: {exc}"}
        else:
            try:
                token = self._mint(proposal["target"]["repo"], proposal["level"])
                result = self._execute(proposal, token, **self._execute_kwargs)
            except (ExecutionError, GitHubAppError) as exc:
                log.warning("proposal %s failed: %s", proposal_id, exc)
                result = {"error": str(exc)}

        self.counters["failed" if result.get("error") else "executed"] += 1
        if self._reporter.report(proposal_id, result):
            self._redis.xack(APPROVED_STREAM, GROUP, entry_id)
            self.counters["acked"] += 1
        else:
            # NOT acked — the entry stays on the PEL; the next cycle redelivers (at-least-once).
            self.counters["unacked"] += 1
            log.error("result for %s undeliverable — leaving entry %s pending", proposal_id, entry_id)
