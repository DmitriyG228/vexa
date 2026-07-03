"""``create_app(agent_api_url, redis_url, webhook_secret, ...) -> FastAPI`` — the vcs ingress.

The UNTRUSTED-EVENT INGRESS for GitHub App webhooks (issues, PRs, comments — attacker-influenceable
input). Flow per ``POST /webhooks/github``:

1. **verify** — X-Hub-Signature-256 over the raw body (constant-time, fail-closed 401);
2. **allowlist** — only the known event types pass (``ping`` answers the handshake);
3. **dedupe** — ``X-GitHub-Delivery`` via redis ``SET NX`` + TTL (a redelivery is acked, not re-run);
4. **persist-first** — the full ingress.v1 Delivery is XADDed to ``vcs:events`` (MAXLEN ~10000,
   approximate) BEFORE any dispatch, so a downstream failure never loses the record;
5. **dispatch** — one event.v1 envelope per matching ``vcs:subs`` subscription is POSTed to
   agent-api ``/events``, carrying the subscription's subject + plan and the OPAQUE source ref
   only — **no payload bytes cross this seam** (the agent unit re-fetches via read-only tools, and
   an event-triggered unit mounts the workspace ``ro``; worst case of any injection is a
   proposal.v1 in the human-gated queue, never an action).

``POST /internal/replay/{delivery_id}`` re-runs dispatch from the stored record against the CURRENT
subscriptions — the recovery path the persist-first ordering buys.

Both effect ports are injectable (``transport`` — an ``httpx.MockTransport`` agent-api;
``redis_client`` — a fakeredis), so the conformance tests drive the SHIPPED app in-process with no
network — the repo's test idiom.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .normalize import ALLOWED_EVENTS, delivery_record
from .subscriptions import subscriptions_for
from .verify import verify_signature

log = logging.getLogger(__name__)

_DEFAULT_AGENT_API_URL = "http://agent-api:8100"
_DEFAULT_REDIS_URL = "redis://redis:6379/0"

EVENTS_STREAM = "vcs:events"
EVENTS_MAXLEN = 10_000
DEDUPE_PREFIX = "vcs:delivery:"
DEDUPE_TTL_SEC = 24 * 3600


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def create_app(
    agent_api_url: Optional[str] = None,
    redis_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    *,
    transport: Optional[httpx.BaseTransport] = None,
    redis_client=None,
) -> FastAPI:
    """Build the ingress app.

    ``agent_api_url`` — the dispatch sink base (env ``VEXA_AGENT_API_URL``, compose
                        ``http://agent-api:8100``).
    ``redis_url``     — the dedupe/stream/subscription backing (env ``REDIS_URL``); ignored when a
                        ``redis_client`` is injected (fakeredis in the tests).
    ``webhook_secret``— the GitHub App shared secret (env ``VEXA_GITHUB_WEBHOOK_SECRET``). Absent
                        secret ⇒ the webhook route fails closed (503) — never an unverified accept.
    ``transport``     — optional httpx transport override so the tests fake agent-api offline.
    """
    base_url = (agent_api_url or os.getenv("VEXA_AGENT_API_URL") or _DEFAULT_AGENT_API_URL).rstrip("/")
    secret = webhook_secret if webhook_secret is not None else os.getenv("VEXA_GITHUB_WEBHOOK_SECRET", "")
    if redis_client is None:
        import redis as _redis

        r = _redis.from_url(redis_url or os.getenv("REDIS_URL") or _DEFAULT_REDIS_URL, decode_responses=True)
    else:
        r = redis_client

    app = FastAPI(title="vexa-vcs-ingress", version="0.12.0")
    # log + count (P18): every non-accept path is a visible, queryable state, never a silent drop.
    counters = {
        "accepted": 0,
        "ignored": 0,
        "duplicate": 0,
        "no_subscription": 0,
        "dispatched": 0,
        "dispatch_failed": 0,
    }
    app.state.counters = counters

    def _dispatch(record: dict) -> dict:
        """Fan the (already persisted) record out: one event.v1 envelope per matching
        subscription, subject + plan stamped from the record's opposite side of the trust seam —
        the routine the OWNER authored, never anything derived from the webhook payload."""
        envelope = record["envelope"]
        subs = subscriptions_for(r, record.get("repo") or "", envelope["name"])
        if not subs:
            counters["no_subscription"] += 1
            log.info(
                "delivery %s (%s on %s): no matching subscription — persisted, not dispatched",
                record["delivery_id"], envelope["name"], record.get("repo"),
            )
        dispatched = 0
        for sub in subs:
            event = dict(envelope)
            event["subject"] = sub["subject"]
            event["plan"] = dict(sub["plan"])
            try:
                with httpx.Client(timeout=10, transport=transport) as client:
                    resp = client.post(f"{base_url}/events", json=event)
                resp.raise_for_status()
                dispatched += 1
                counters["dispatched"] += 1
            except httpx.HTTPError as exc:
                # Persist-first: the record already sits on vcs:events; /internal/replay recovers.
                counters["dispatch_failed"] += 1
                log.warning(
                    "delivery %s: dispatch to %s failed for subject %s: %s",
                    record["delivery_id"], base_url, sub.get("subject"), exc,
                )
        return {
            "delivery_id": record["delivery_id"],
            "event": envelope["name"],
            "subscriptions": len(subs),
            "dispatched": dispatched,
        }

    # ── liveness probe (compose healthcheck) — no auth, no redis, no agent-api hop ──────────────
    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "service": "vcs-ingress"}

    # ── the webhook front door ───────────────────────────────────────────────────────────────────
    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(request: Request):
        raw = await request.body()
        if not secret:
            # Fail-closed: an ingress with no configured secret must not accept anything (P18 —
            # a reported state, never a silent unverified accept).
            raise HTTPException(status_code=503, detail="webhook secret not configured")
        if not verify_signature(secret, raw, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=401, detail="missing or invalid X-Hub-Signature-256")

        event = (request.headers.get("X-GitHub-Event") or "").strip()
        if event not in ALLOWED_EVENTS:
            counters["ignored"] += 1
            log.info("ignoring non-allowlisted event type %r", event)
            return Response(status_code=204)
        if event == "ping":
            return JSONResponse({"status": "pong"})

        delivery_id = (request.headers.get("X-GitHub-Delivery") or "").strip()
        if not delivery_id:
            raise HTTPException(status_code=400, detail="missing X-GitHub-Delivery")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON")
        if not r.set(DEDUPE_PREFIX + delivery_id, "1", nx=True, ex=DEDUPE_TTL_SEC):
            counters["duplicate"] += 1
            return JSONResponse({"status": "duplicate", "delivery_id": delivery_id})

        record = delivery_record(
            event=event,
            payload=payload,
            delivery_id=delivery_id,
            payload_digest=hashlib.sha256(raw).hexdigest(),
            received_at=_utcnow(),
        )
        if record is None:
            # Allowlisted type, unmapped (event, action) — noise (issues.labeled, pr.synchronize…):
            # acked so GitHub does not retry, counted, not persisted.
            counters["ignored"] += 1
            log.info("delivery %s: unmapped (%s, %s) — ignored", delivery_id, event, payload.get("action"))
            return Response(status_code=204)

        # PERSIST-FIRST: the durable record lands before any dispatch attempt.
        r.xadd(EVENTS_STREAM, {"record": json.dumps(record, sort_keys=True)},
               maxlen=EVENTS_MAXLEN, approximate=True)
        counters["accepted"] += 1
        return _dispatch(record)

    # ── replay — re-run dispatch from the stored record (the persist-first payoff) ──────────────
    @app.post("/internal/replay/{delivery_id}")
    def replay(delivery_id: str):
        for _entry_id, fields in r.xrange(EVENTS_STREAM):
            try:
                record = json.loads(fields.get("record", "{}"))
            except ValueError:
                continue
            if record.get("delivery_id") == delivery_id:
                return _dispatch(record)
        raise HTTPException(status_code=404, detail="unknown delivery_id")

    return app
