# ingress.v1 — the untrusted-event ingress record (GitHub webhooks → opaque refs)

GitHub App webhooks (issues, PRs, comments) are **attacker-influenceable input**. `vcs-ingress`
receives them, persists them, and dispatches **opaque references** into the agent runtime — **no
payload bytes cross the seam**. The fired unit re-fetches content via read-only tools inside its
container; an event-triggered unit already mounts the workspace `ro` (`units.mode_for`), so the
worst case of any injected content is a bad `proposal.v1` in the human-gated queue — never an action.

## The Delivery record

One webhook = one `Delivery`, XADDed to the `vcs:events` stream (MAXLEN ~10000, approximate):

- `delivery_id` — the `X-GitHub-Delivery` guid: the dedupe key (redis `SET NX` + TTL) and the
  replay handle.
- `payload_digest` — sha256 of the **raw body**: the audit anchor. The payload itself is
  deliberately **not** re-stored (the no-payload-across-the-seam rule) — the digest is what ties
  the record back to what GitHub signed and sent.
- `signature_ok` — always `true` for a persisted record: an unverified delivery is rejected `401`
  **before** persistence (`X-Hub-Signature-256`, constant-time compare).
- `envelope` — the normalized `event.v1` Event this delivery emits. The stored form carries the
  shared fields (`name`, `occurred_at`, `source`); `subject` + `plan` are stamped **per matching
  subscription** at dispatch, and the dispatched form conforms to `event.v1` (validated by path at
  the agent-api seam — this contract does not duplicate it).

Event names derive from `(event, action)`: `vcs.issue.opened` · `vcs.issue.commented` ·
`vcs.pr.opened` · `vcs.pr.commented` · `vcs.pr.review`. The `source.uri` is the opaque ref only —
`github://<owner>/<repo>/issues/<n>` or `.../pull/<n>` — never title/body text.

## Persist-first replay

The record lands on `vcs:events` **before** any dispatch attempt, so a dispatch failure (agent-api
down, a network blip) never loses the delivery: `POST /internal/replay/{delivery_id}` re-runs
normalize+dispatch from the stored record against the **current** `vcs:subs` subscriptions. A
delivery with no matching subscription is persisted but not dispatched (logged + counted) — the
record is still there when a subscription lands later.

**Status: sealed.** `gate:schema` validates the goldens (`Delivery.issue-opened`,
`Delivery.pr-opened`) against `#/$defs/Delivery`.
