# vcs-executor — the credentialed executor (approved proposals → GitHub, nothing else)

## Purpose

The **privileged half of the proposal.v1 human gate**, and the ONLY holder of GitHub credentials
in the system. Agent routines never touch GitHub: a worker EMITS a proposal, a HUMAN decides it,
and this service consumes the approved feed and executes it — **nothing else, no interpretation**.
The credential and the gate sit on opposite sides of the agent (P15): the worker container holds
no GitHub token, and the token-holder executes nothing a human didn't approve — a prompt-injected
unit can at worst *ask*.

## Flow (per `proposal:approved` stream entry)

1. **read** — `XREADGROUP` on `proposal:approved` (this service creates + owns the `vcs-executor`
   consumer group; agent-api only XADDs the feed). Own pending entries drain first (redelivery).
2. **validate** — the full proposal re-validates against the sealed `proposal.v1` schema **by
   path** (P8) — the stream is a transport, the contract is the truth.
3. **policy** — the fail-loud pre-credential gate (defense in depth): the level↔action binding
   re-checked in code; only `approved` + `decision.by`; the L3 head branch is
   executor-CONSTRUCTED as `vexa/<proposal-id>-<slug>` and refused if protected
   (`VEXA_PROTECTED_BRANCHES`, default `main,master,0.12`), wrong-prefixed, or equal to base;
   `--force` is refused at the git runner. **A violating proposal is marked failed — the mint
   function is never called.**
4. **mint** — a fresh GitHub App installation token per proposal, scoped twice: to the TARGET
   repo (`repositories: [repo]`) and to the proposal's level — L2 `{issues:write,
   pull_requests:write}`, L3 + `{contents:write}`. Tokens redact themselves (the BrokeredSecret
   contract) — the raw value is reachable only via `reveal()`.
5. **execute** — L2 via REST (comment · label · close · open_issue). L3 git flow: shallow clone
   (token-in-remote-URL, ephemeral argv, never a persisted remote) → branch from the declared
   base → `diff` patches ride an mbox through `git am` (Author/AuthorDate/Signed-off-by
   preserved VERBATIM from format-patch mails) · `content` patches commit with the payload's
   title/body → `git interpret-trailers --if-exists doNothing --trailer "Origin: <repo>@<sha>"`
   per commit when the proposal carries origin provenance (idempotent) → push → `open_pr` into
   the declared base, the PR body carrying the proposal id + approver as the audit cross-link.
6. **report** — the outcome POSTs to agent-api `POST /internal/proposals/{id}/executed`
   (shared-secret bearer) — **agent-api stays the single writer of proposal state** (P23).
   Delivered (200) → `XACK`; 409 (already settled server-side — the idempotency check) → `XACK`;
   undeliverable → retry with backoff, then leave the entry pending for redelivery
   (**at-least-once**).

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| reads | redis `proposal:approved` stream | `XREADGROUP`/`XACK` (group owned here) | full approved `proposal.v1` records — **written only by agent-api** |
| calls | agent-api (`VEXA_AGENT_API_URL`) | `POST /internal/proposals/{id}/executed` | the ExecutionRecord fields (`result_url`/`sha`/`error`) under the `VEXA_EXECUTOR_RESULT_TOKEN` bearer |
| calls | GitHub (`api.github.com` + git push) | App JWT → installation token → REST/git | ONLY human-approved actions, under a per-proposal doubly-scoped token |

## Configuration

`VEXA_GITHUB_APP_ID` + `VEXA_GITHUB_APP_PRIVATE_KEY_PATH` (a mounted secret FILE, never an
env-inlined key) · `VEXA_EXECUTOR_RESULT_TOKEN` (the report-back bearer; must equal agent-api's) ·
`VEXA_AGENT_API_URL` (default `http://agent-api:8100`) · `REDIS_URL` (default
`redis://redis:6379/0`) · `VEXA_PROTECTED_BRANCHES` (default `main,master,0.12`) ·
`VEXA_GITHUB_API_URL` (GHE override) · `HOST`/`PORT` (default 8021) · `LOG_LEVEL`.

Fail-closed assembly: an incompletely configured executor serves `/health`, logs the gap loudly,
and consumes NOTHING — approved entries stay on the stream, no approval is burned (P18).

## Isolated evaluation

```bash
uv run pytest -q        # uv manages this package's own venv/deps
```

`tests/` runs offline against the shipped modules: agent-api + GitHub behind
`httpx.MockTransport`, redis via `fakeredis`, the App key a test-generated RSA pair, and the L3
"remote" a **local bare git repo** — conformance is validated against the sealed `proposal.v1`
schema **by path** (never importing agent code).

## Licensing

All deps are Category A (ADR-0004): `fastapi` (MIT), `httpx` (BSD-3), `redis` (MIT), `PyJWT`
(MIT), `cryptography` (Apache-2.0/BSD-3), `jsonschema` (MIT), `referencing` (MIT), `fakeredis`
(BSD-3), `uvicorn` (BSD-3). Pinned in `uv.lock`.
