# DOCS-GAPS — 0.12 documentation debt checklist

Companion to [PARITY-MAIN.md](PARITY-MAIN.md) and the parity section in
[`docs/changelog.mdx`](docs/changelog.mdx). Every claim below was verified against this tree on
2026-07-04 (route tables, module code, or a live-edge probe) — not inferred from planning docs.
Check items off as pages land; delete the file when it's empty.

Items checked 2026-07-05 landed in the release docs sweep (`docs/release-012-operator-guide`).

## 1 · Capability shipped, no corpus page (write these)

- [x] **Terminal workbench** (`clients/terminal`) — landed as `docs/clients/terminal.mdx`.
- [x] **Webhooks user guide** — landed as `docs/how-to/webhooks.mdx` (HMAC recipe incl. the
      `ts.payload` breaking change vs 0.10, SSRF guard, retry/dead-letter, `PUT`/`GET /user/webhook`).
- [x] **Token scoping** — landed in `docs/authentication.mdx` (route→scope enforcement table;
      mint format/multi-scope were already documented there).
- [x] **Teams `passcode`** — landed in `docs/api/meetings.mdx` + `docs/how-to/send-a-bot.mdx`.
      Verified mechanism: `POST /bots` has **no passcode field** — the passcode travels inside
      `meeting_url` (`?p=` for Teams, `?pwd=` for Zoom); documented accordingly.
- [x] **Vexa Lite** (`deploy/lite`) — landed as `docs/deployment/lite.mdx` (supported path per
      Decision 1). The earlier `make lite` discrepancy is **resolved in-tree**: the root
      `Makefile` has the `lite` target (added by PR #42).
- [x] **Helm chart** (`deploy/helm/charts/vexa`) — landed as `docs/deployment/helm.mdx`
      (RUNTIME_BACKEND=k8s, bots as Pods, values, model-auth Secret per PR #52).
- [x] **MCP service** (`core/meetings/services/mcp`) — landed as `docs/api/mcp.mdx` (Decision 2:
      standalone port interim, one gateway-fronted server long-term).
- [x] **`STORAGE_BACKEND`** — landed in `docs/configuration.mdx`. Verified against this tree:
      `STORAGE_BACKEND` is a metadata **label** (default `minio`), the real switch is the
      S3-compatible endpoint resolution (`S3_*` → `MINIO_*` fallbacks) in
      `meeting_api/__main__.py` — main's storage matrix was adapted to that truth, not ported
      verbatim.
- [x] **Scheduling intent** — landed in `docs/api/meetings.mdx`
      (`PUT /meetings/{platform}/{native}/intent`, `intent: idle|scheduled` + `at`). Still open:
      this route (and `GET /user/webhook`, the `/agent/*` prefix) is **gateway surface beyond the
      sealed api.v1** — decide whether the next contract re-seal should include it.

## 2 · Corrections (docs said one thing, tree says another)

- [x] `PARITY-MAIN.md` §4 — "`DELETE /bots` unmounted / 404s" was **stale**: the stop route is
      wired (`lifecycle/stop_router.py` via `meeting_api/app.py`) and tested. Fixed in this PR;
      the corpus `api/meetings.mdx` was already correct.
- [ ] The earlier docs-plan claim that "`PATCH`/`DELETE /meetings`, `GET`/`PUT /recording-config`,
      `DELETE /recordings/{id}`, media `…/download` are sealed **and built** but undocumented" is
      half right: **sealed yes, built no.** None of them is mounted at the gateway and meeting-api
      has no handlers (live probes 404). They belong in the parity table
      (contract-sealed, not wired), not the docs plan — do NOT write pages that claim they work.
      (Standing guardrail — keep until those routes land or are dropped from the contract.)

## 3 · Port-from-main list (content that is still true for 0.12)

From the main-docs convergence map (Lane F, 2026-07-04); port into the corpus, re-verifying env
names and selectors against this tree while porting:

- [x] `meeting-ids.mdx` — folded into `docs/api/meetings.mdx` as the per-platform id/passcode
      table (verified against the MCP `link_parser.py` rules), rather than a standalone page.
- [ ] `errors-and-retries.mdx` — retry/idempotency guidance → merge into `api/errors.mdx`.
- [ ] `speaker-identification.mdx` — DOM-based speaker correlation mechanism + limits.
- [ ] `transcription-quality.mdx` — engine matrix, language support, hallucination filtering.
- [ ] `platforms/google-meet.mdx` — admission model + failure modes (0.12 already carries the
      typed denial/lobby-timeout distinction — document it).
- [ ] `platforms/microsoft-teams.mdx` — URL-format table + passcode extraction (the passcode
      essentials landed in `api/meetings.mdx`; the full platform page is still open).
- [ ] `platforms/zoom.mdx` — Zoom **web client** path only (0.12 has no BYO OBF/ZAK tokens and no
      native SDK branch — do not port `zoom-app-setup.mdx`).
- [x] `webhooks.mdx` + `local-webhook-development.mdx` — landed as `docs/how-to/webhooks.mdx`
      (local-dev note folded in: the SSRF guard blocks private receivers → use a public tunnel).
- [ ] `scaling.mdx` (partial) — per-bot resource sizing / one-browser-per-bot model; the Helm half
      now has a real in-tree target (§1).
- [ ] `self-hosted-management.mdx` (partial) — the per-endpoint admin reference tables
      (create user, `max_concurrent_bots`, token mint incl. `scopes`).
- [ ] `websocket.mdx` (partial) — segment-merge algorithm + keepalive detail as an appendix to
      `how-to/stream-transcript.mdx`.
