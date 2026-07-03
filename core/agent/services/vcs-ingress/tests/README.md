# tests — vcs-ingress service (autonomous, in-process)

`uv run pytest -q`. No docker, no network: `conftest.py` injects BOTH effect ports into
`create_app(...)` — agent-api behind `httpx.MockTransport` (records every dispatched envelope) and
redis as `fakeredis` — so every test drives the SHIPPED flow. Contract conformance is validated
**by path** against the sealed `ingress.v1` + `event.v1` schemas (the schema-not-import idiom);
this package never imports agent code.

- **`test_health.py`** — gate:health: `/health` → 200 `{status:"ok", service:"vcs-ingress"}`,
  no downstream hop.
- **`test_verify.py`** — L2 unit: X-Hub-Signature-256 verification (stdlib `hmac.compare_digest`,
  constant-time): valid accepts; tampered/missing/malformed/wrong-secret reject; empty secret
  fails closed.
- **`test_normalize.py`** — L1 contract: the `(event, action)` → `vcs.*` name table and the
  opaque-uri shapes; the Delivery ≡ `ingress.v1` and the stamped envelope ≡ `event.v1` by path;
  the injection canary (payload title/body text) never rides the record.
- **`test_app.py`** — L3 seam: verify → allowlist → dedupe → persist-first → per-subscription
  dispatch → replay. Accepts persist a conformant Delivery and fan one envelope per matching
  `vcs:subs` record; 401 (tampered/missing sig) leaves no state; 204 for non-allowlisted /
  unmapped noise; duplicates are idempotent; a dispatch failure never loses the record and
  `/internal/replay/{id}` recovers it against the CURRENT subscriptions; missing secret → 503.

`fixtures/` holds the golden GitHub payloads (signed with real computed HMACs in the tests).
