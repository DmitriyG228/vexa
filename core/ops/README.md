# ops — the platform-support domain

**What lives here: services that support the PLATFORM and the people running it** — not the
product domains. `meetings/` captures conversations, `agent/` executes on them, `identity/`,
`gateway/` and `runtime/` carry them; **`ops/` is the layer that helps humans deploy, operate
and improve all of that**: help surfaces, operational telemetry, the feedback loops that turn
user friction into fixes and docs.

The boundary test for whether a service belongs here: *its user is an operator/integrator of
vexa (or the vexa maintainers), not a meeting participant or an agent subject* — and removing
it must never break a product flow (ops services are additive; product domains never depend on
`ops/`).

| Service | Role |
|---|---|
| [`services/help-mcp/`](services/help-mcp/) | the public help companion (MCP): answers deploy/integration questions from two always-labeled tiers — `doc` (cited docs corpus) and `operational` (live GitHub triage) — and turns its own question stream into doc-gap telemetry |

Same bounded-context rules as every domain one level down (P1–P3): internals import only their
own code, another domain's `contracts/`, and `core/runtime/contracts` — never another domain's
internals. `ops/` currently publishes **no contract**: its one data carrier (the `help:questions`
stream) is single-writer internal; it freezes as `help-log.v1` the day a second writer appears.
