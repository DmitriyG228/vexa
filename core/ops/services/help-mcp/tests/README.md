# tests — help-mcp service (autonomous, in-process)

`uv run pytest -q`. No docker, no network: `conftest.py` injects EVERY effect port into
`create_app(...)` — GitHub + agent-api behind `httpx.MockTransport` (each recording every hop),
redis as `fakeredis` (wrapped to capture XADD kwargs), a tmp 4-file mdx corpus, and a settable
clock for the TTL cache. The dispatched `help.escalated` envelope is validated **by path**
against the sealed `event.v1` schema (the schema-not-import idiom); the deploy-guide selectors
are additionally pinned against the REAL shipped docs corpus (offline — files in this repo).

- **`test_health.py`** — gate:health: `/health` → 200 `{status:"ok", service:"help-mcp"}`, no
  downstream hop, no telemetry write.
- **`test_mcp_surface.py`** — the `/mcp` mount + exactly the 5 tools; every tool description
  discloses retention; `review_question_log` is marked maintainer-facing.
- **`test_envelope.py`** — the shared envelope across ALL five tools: provenance rule +
  retention notice present; every result labeled `doc`/`operational`.
- **`test_docs_index.py`** — parsing (frontmatter, headings, code-fence immunity) + relevance
  GOLDENS (query → expected section) on the fixture corpus + a real-corpus smoke.
- **`test_deploy_guide.py`** — every target's selectors resolve against the shipped corpus
  (anti-drift); lite/helm degrade to honest operational notes; unknown target → 422.
- **`test_known_issues.py`** — the triage-label fetch (`status: accepted,type: bug` — spaces),
  TTL cache via the injected clock, anonymous/Bearer split, query/area filters, rate-limit +
  network degrades (failures are never cached).
- **`test_escalate.py`** — dual mode (draft/filed), the event.v1 envelope BY SCHEMA PATH in
  both modes, GitHub-write fallback to draft, agent-api-down degrade.
- **`test_question_log.py`** — the XADD shape (`{ts, tool, question, top_paths,
  answered_from_docs}`, MAXLEN ~5000 approximate) on every tool call; since/limit read-back;
  redis-absent degrade.
