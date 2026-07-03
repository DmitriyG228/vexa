# tests — vcs-executor service (autonomous, in-process)

`uv run pytest -q`. No docker, no network: `conftest.py` fakes EVERY effect — agent-api and
GitHub behind `httpx.MockTransport` recorders, redis via `fakeredis`, the GitHub App key a
test-generated RSA pair, and the L3 "remote" a **local bare git repo**. Conformance is validated
**by path** against the sealed `proposal.v1` schema (the schema-not-import idiom); this package
never imports agent code.

- **`test_health.py`** — gate:health: `/health` → 200 `{status:"ok", service:"vcs-executor"}`,
  no downstream hop, no credential.
- **`test_policy.py`** — L2 unit: the fail-loud pre-credential gate — protected-branch refusal
  (defaults + configured list), wrong prefix / foreign proposal id / head==base, the code-side
  level↔action re-check, non-approved / approver-less refusal, `--force` structurally refused at
  the git runner.
- **`test_github_app.py`** — L2 unit: RS256 app-JWT claims verify under the test public key;
  the installation token requests exactly `repositories: [repo]` + the level's permission set
  (L2 never gets `contents`); tokens redact themselves everywhere but `reveal()`.
- **`test_actions.py`** — L3 seam against local bare remotes: branch `vexa/<id>-<slug>` created
  from base; the `git am` path preserves Author/AuthorDate/Signed-off-by VERBATIM; the `Origin:`
  trailer lands exactly once (idempotent when already present); `content` patches commit with
  the payload metadata; the push lands; `open_pr` carries the proposal id + approver; every L2
  REST action hits exactly its endpoint; git errors never leak the token.
- **`test_consumer.py`** — L3 seam: group creation idempotent + owned; report delivered → ack;
  409 → ack (server-side status check = idempotency); undeliverable → backoff, NO ack,
  redelivery re-executes (at-least-once); schema/policy/status violations reported FAILED with
  the mint seam provably never called; malformed entries are poison (acked + counted).
- **`test_app.py`** — assembly: the lifespan starts/stops the consumer thread; an unconfigured
  app serves health but consumes nothing (fail-closed, the gap visible on `app.state`).
