# vcs_executor (package) — create_app · consumer · policy · github_app · actions

The credentialed-executor logic, injectable. Public surface is `__init__.py`:
**`create_app(agent_api_url, redis_url, redis_client=…, agent_api_transport=…,
github_transport=…, clock=…, consumer=…, consume=…)`**. Modules:

- **`app.py`** — assembly: `/health` (pure liveness) + the lifecycle-managed consumer thread.
  Fail-closed: the consumer starts ONLY when fully configured (App id + key file + report
  secret); otherwise the gap is logged loudly and NOTHING is consumed (P18).
- **`consumer.py`** — the pipeline: `XREADGROUP proposal:approved` (group created idempotently,
  owned here) → proposal.v1 **by schema path** (P8) → the policy gate → mint → execute → report
  to agent-api `POST /internal/proposals/{id}/executed` (agent-api stays the ONE writer of
  proposal state). Delivered/409 → `XACK`; undeliverable → backoff, no ack, redelivery
  (at-least-once; server-side status check = idempotency).
- **`policy.py`** — the fail-loud PRE-CREDENTIAL gate: level↔action re-checked in code (defense
  in depth); `approved` + `decision.by` only; the L3 head is CONSTRUCTED
  (`vexa/<proposal-id>-<slug>`) and refused when protected / wrong-prefixed / equal to base. A
  refused proposal is reported failed — the mint function is never called.
- **`github_app.py`** — App auth: RS256 app JWT (~10 min) → installation per target repo →
  installation token scoped to `repositories: [repo]` + the level's permission set (L2
  `issues/pull_requests: write`; L3 + `contents: write`). Tokens redact themselves
  (`reveal()`-only, the BrokeredSecret contract).
- **`actions.py`** — L2 REST (comment · label · close · open_issue); L3 git flow (shallow clone
  with an EPHEMERAL token-in-URL, branch from base, `git am` mbox — format-patch mails verbatim,
  `content` commits with payload metadata, the idempotent `Origin:` provenance trailer, plain
  push — the runner structurally refuses `--force`), then `open_pr` with the proposal id +
  approver in the PR body.
- **`__main__.py`** — `python -m vcs_executor`, the production entrypoint (compose CMD).

This package is self-contained: it consumes `proposal.v1` by schema path, never by importing
agent-api code (the schema-not-import idiom; gate:isolation-py).
