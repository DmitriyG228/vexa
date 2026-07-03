# help-mcp — the vexa help companion (the dynamic help surface)

A **public-facing MCP service** users' coding agents connect to while deploying/integrating
vexa (`http://<host>:18011/mcp`, streamable HTTP). It answers from **two knowledge tiers and
ALWAYS labels which** — the provenance rule every response envelope carries:

- **`doc`** — cited from the **shipped docs corpus** (path + heading): documented, supported
  behavior. The image vendors `docs/docs` → `/app/docs` (`DOCS_ROOT`), indexed in-memory at
  startup by ~100 lines of stdlib (heading sections; TF with heading/title boost; fenced code
  blocks are opaque). No search dependency — the corpus is ~37 files / ~288 KB (P17).
- **`operational`** — **live state that is NOT yet documentation**: open maintainer-accepted
  bugs on the public tracker (labels `status: accepted` + `type: bug`, 15-minute TTL cache),
  repo facts the docs don't cover yet, and the question log itself.

**Read-only v1.** The service mutates exactly two things: its own telemetry stream and —
config-gated — escalation issues (below). It holds no vexa credential, no DB, and never
reaches past agent-api's public `/events` sink.

## The five tools (each a FastAPI route; `operation_id` = tool name)

| Tool | Tier | What it does |
|---|---|---|
| `search_vexa_docs(query, max_results=5)` | doc | section-level lexical search; results cite path + heading |
| `get_deploy_guide(target: compose\|lite\|helm\|overview)` | doc + operational | whole doc sections per target; undocumented targets (lite/helm today) degrade to explicit operational notes — never invented text |
| `check_known_issues(query?, area?)` | operational | open triaged bugs from `Vexa-ai/vexa`; 15-min cache; rate-limit/network failures degrade to a note |
| `escalate(question, environment_summary, docs_consulted[])` | operational | dual mode: with `HELP_GITHUB_TOKEN` files a structured issue and returns its URL; without, returns the fully-formed draft. Both modes emit a `help.escalated` event.v1 to agent-api `/events` (best-effort) |
| `review_question_log(since?, limit=100)` | operational | **maintainer-facing**: reads the telemetry stream back for the ops doc-gap routine |

## Telemetry = the product's feedback loop

Every tool call XADDs `{ts, tool, question, top_paths, answered_from_docs}` to the redis stream
`help:questions` (MAXLEN ~5000, approximate — retention is finite by construction). Recurring
friction in that stream IS the doc-gap signal the ops workspace reviews. **Best-effort by
design**: redis absence is logged and swallowed — telemetry never fails an answer.

Single writer: help-mcp. The stream is v1-internal (no published contract); it **freezes as a
published `help-log.v1` contract the day a second writer appears** (P23).

## Privacy stance

Questions are **retained** (bounded, ~5000 entries) solely to find documentation gaps. Callers
must send **no code, no secrets, no personal data** — and because escalation issues land on a
PUBLIC tracker, `environment_summary` must be facts only. This is not just a README promise:
**every tool docstring — the MCP tool description the caller's agent reads — discloses the
retention** (pinned by `tests/test_mcp_surface.py`).

## Config (env)

| Var | Meaning |
|---|---|
| `DOCS_ROOT` | docs corpus root (image default `/app/docs`) |
| `HELP_GITHUB_TOKEN` | OPTIONAL: lifts anonymous GitHub rate limits and switches `escalate` to filed mode |
| `HELP_GITHUB_REPO` | tracker repo (default `Vexa-ai/vexa`) |
| `VEXA_OPS_SUBJECT` | event.v1 subject `help.escalated` events are attributed to (default `ops`) |
| `VEXA_AGENT_API_URL` | the `/events` sink (compose `http://agent-api:8100`) |
| `REDIS_URL` | the question-log backing (compose `redis://redis:6379/0`) |

Compose binds `127.0.0.1:${HELP_MCP_HOST_PORT:-18011} → 8011`; **public exposure is a
reverse-proxy concern** (same stance as the gateway). Build context is the carve root — the
Dockerfile vendors `docs/docs` into the image so the "doc" tier cites exactly what it ships.

## Run / test

```bash
uv run pytest -q          # offline: fake GitHub + agent-api (MockTransport), fakeredis, tmp corpus
python -m help_mcp        # serve (env above; port 8011)
```
