# help_mcp — module map

- **`app.py`** — `create_app(...)`: the five tool routes (`operation_id` = MCP tool name,
  docstring = the tool description the caller's agent reads — each disclosing retention), the
  shared response envelope (provenance rule + retention notice), the `/mcp` mount, `/health`.
- **`docs_index.py`** — the "doc" tier: heading-section splitter (fenced code blocks are
  opaque) + the stdlib TF/boost/coverage lexical index over `DOCS_ROOT`. No search dependency
  (P17) — the corpus is ~37 files.
- **`guide.py`** — `get_deploy_guide`'s target → `(path, heading)` selector map; missing
  coverage degrades to explicit `operational` notes, never invented text.
- **`gh.py`** — the "operational" tier: TTL-cached triaged-bug reads (`status: accepted` +
  `type: bug`), the config-gated escalation-issue write, the structured issue draft.
- **`telemetry.py`** — the `help:questions` stream (XADD per tool call, MAXLEN ~5000
  approximate; best-effort by design) + the maintainer read-back.
- **`__main__.py`** — `python -m help_mcp` (the compose CMD).
