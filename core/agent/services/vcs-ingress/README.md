# vcs-ingress — the untrusted-event ingress (GitHub webhooks → opaque refs)

## Purpose

GitHub App webhooks (issues, PRs, comments) are **attacker-influenceable input**. This service
receives them, persists them, and dispatches **opaque references** into the agent runtime — **no
payload bytes cross the seam**. The fired unit re-fetches content via read-only tools inside its
container, and an event-triggered unit already mounts the workspace `ro` (`units.mode_for` derives
propose-only from the trigger), so the worst case of ANY injected content is a bad `proposal.v1`
in the human-gated queue — never an action. The credential and the gate stay on opposite sides of
the agent (the proposal-queue trust posture); this service holds no GitHub token at all.

## Flow (`POST /webhooks/github`)

1. **verify** — `X-Hub-Signature-256` HMAC over the raw body, stdlib `hmac.compare_digest`
   (constant-time); missing/invalid → **401**, nothing persisted.
2. **allowlist** — `ping` · `issues` · `issue_comment` · `pull_request` · `pull_request_review` ·
   `pull_request_review_comment`; anything else → **204** ignored (counted).
3. **dedupe** — `X-GitHub-Delivery` via redis `SET NX` + 24h TTL; a redelivery is acked
   (`{"status":"duplicate"}`), never re-run.
4. **persist-first** — the full `ingress.v1` Delivery (identity + sha256 payload digest + the
   normalized envelope) is `XADD`ed to the **`vcs:events`** stream (MAXLEN ~10000, approximate)
   **before** any dispatch — a downstream failure never loses the record.
5. **dispatch** — one `event.v1` envelope per matching **`vcs:subs`** subscription is POSTed to
   agent-api `/events`, `subject` + `plan` stamped from the subscription (the routine the OWNER
   authored), `source.uri` the opaque `github://<owner>/<repo>/issues/<n>` / `.../pull/<n>` ref
   only. No subscription → persisted, not dispatched (log + count).

`POST /internal/replay/{delivery_id}` re-runs dispatch from the stored record against the
**current** subscriptions — the recovery path persist-first buys. `/health` is the liveness probe.

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| serves | GitHub (behind a reverse proxy) | `POST /webhooks/github` | signed webhook deliveries; only the signature, ids, repo, and numbers are used |
| writes | redis `vcs:events` stream | `XADD` (single writer) | `ingress.v1` Delivery records — digest-anchored, payload never re-stored |
| reads | redis `vcs:subs` hash | `HGET` | repo → [(subject, plan, access)] — **written only by agent-api** (the workspace-routine reconciler) |
| calls | agent-api (`VEXA_AGENT_API_URL`) | `POST /events` | `event.v1` envelopes — opaque ref + the subscription's plan; **no payload bytes** |

## Event names

`(event, action)` → `vcs.issue.opened` · `vcs.issue.commented` · `vcs.pr.opened` ·
`vcs.pr.commented` (a comment on a PR-backed issue, or a review comment) · `vcs.pr.review`.
Unmapped actions (labels, syncs, deletes) are acked 204 and not persisted.

## Configuration

`VEXA_GITHUB_WEBHOOK_SECRET` (absent ⇒ the webhook route fails closed 503) ·
`VEXA_AGENT_API_URL` (default `http://agent-api:8100`) · `REDIS_URL`
(default `redis://redis:6379/0`) · `HOST` / `PORT` (default 8020) · `LOG_LEVEL`.

Compose binds `127.0.0.1:${VCS_INGRESS_HOST_PORT:-18020} → 8020`; **production exposure to GitHub
is a reverse-proxy concern** (TLS + a public route to `/webhooks/github`), not this service's.

## Isolated evaluation

```bash
uv run pytest -q        # uv manages this package's own venv/deps
```

`tests/` runs in-process against `create_app(...)` with BOTH ports injected (agent-api behind
`httpx.MockTransport`, redis via `fakeredis`) and validates conformance against the sealed
`ingress.v1` + `event.v1` schemas **by path** (never importing agent code). Levels: **L1**
contract (normalization ≡ both schemas; the injection canary never crosses) · **L2** unit
(signature verification) · **L3** seam (verify → allowlist → dedupe → persist-first → dispatch →
replay; fail-closed 401/503; idempotent redelivery).

## Licensing

All deps are Category A (ADR-0004): `fastapi` (MIT), `httpx` (BSD-3), `redis` (MIT),
`fakeredis` (BSD-3), `jsonschema` (MIT), `referencing` (MIT), `uvicorn` (BSD-3). Pinned in `uv.lock`.
