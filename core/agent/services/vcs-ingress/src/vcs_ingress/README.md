# vcs_ingress (package) — create_app · verify · normalize · subscriptions

The untrusted-event ingress logic, injectable. Public surface is `__init__.py`:
**`create_app(agent_api_url, redis_url, webhook_secret, transport=…, redis_client=…)`**. Modules:

- **`app.py`** — the flow: verify → allowlist → dedupe (`SET NX` + TTL) → **persist-first**
  (`XADD vcs:events`, MAXLEN ~10000 approximate) → per-subscription dispatch to agent-api
  `/events`; plus `/internal/replay/{delivery_id}` (re-dispatch from the stored record) and
  `/health`. Both effect ports (httpx transport, redis client) are injected in tests.
- **`verify.py`** — `X-Hub-Signature-256` HMAC verification: stdlib `hmac.compare_digest`
  (constant-time, no new deps), fail-closed on missing/malformed headers.
- **`normalize.py`** — GitHub payload → `ingress.v1` Delivery → `event.v1` envelope. The
  `(event, action)` → `vcs.*` name table and the opaque `github://` uri derivation; **no
  title/body text ever rides the output** (the payload is anchored by sha256 digest only).
- **`subscriptions.py`** — the READ side of the `vcs:subs` hash (repo → [(subject, plan,
  access)]); **agent-api is the only writer** (its workspace-routine reconciler compiles
  `on: vcs.*` routines into it).
- **`__main__.py`** — `python -m vcs_ingress`, the production entrypoint (compose CMD): serves
  `create_app()` with `VEXA_AGENT_API_URL` / `REDIS_URL` / `VEXA_GITHUB_WEBHOOK_SECRET` /
  `HOST` / `PORT` from env.

Holds no GitHub credential — the human gate (proposal.v1) and the token sit on the other side of
the agent; this service can only *report* events, never act on GitHub.
