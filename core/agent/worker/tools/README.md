# worker/tools — the worker-side MCP tool servers

Small stdio MCP servers the unit's toolbelt attaches (a `tools-seed/` descriptor names the launch,
`ToolRegistry` resolves it into the turn's `.mcp.json`). They run INSIDE the isolated worker
container and hold **no external credential** — anything privileged is reached by POSTing back to
the control plane, authenticated by the per-dispatch identity token the runtime injected.

| server | tool | does |
|---|---|---|
| `proposals_mcp.py` | `propose_vcs_action` | assembles a `proposal.v1` Proposal (level DERIVED from the action, subject/origin from the dispatch env) and POSTs it to `$VEXA_AGENT_API_URL/internal/proposals` with the `$VEXA_AGENT_IDENTITY_TOKEN` bearer. Nothing executes — a human decides; a separate credentialed executor acts on approvals. |

The descriptor grants `propose_vcs_action` as `auto` (no per-call approval to *emit*): proposing is
free by design — the human gate sits at **approval**, and the credential sits with the executor, so
the worst a prompt-injected unit can do here is ask. Deliberately minimal protocol (newline-delimited
JSON-RPC 2.0: initialize / tools/list / tools/call) so the worker image needs no MCP SDK dep.
