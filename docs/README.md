# docs — the corpus

This directory **is** the documentation corpus — the Mintlify site (`docs.json` is the nav,
`index.mdx` the landing page) plus the engineering references that govern the build. One level,
no nesting: `https://github.com/DmitriyG228/vexa/tree/0.12/docs` is what you see on the docs
site.

**The published site** (product + operator + contributor altitude):

- `index.mdx` · `quickstart.mdx` · `authentication.mdx` — start here
- `how-to/` — task guides (send a bot, stream, recordings, webhooks, agents)
- `clients/` — the terminal workbench
- `core/` · `concepts.mdx` — the backend domains and the glossary
- `architecture/` — how dispatch / execution / streaming / persistence / trust / the gates work
- `api/` — the API references (meetings · agent · MCP · errors)
- `deployment.mdx` + `deployment/` — compose · lite · helm; `configuration.mdx` · `troubleshooting.mdx` · `principles.mdx`
- `decisions.mdx` — how we do things (the standing maintainer-ruled policies)
- `roadmap/` — where we go (approach · stages · status · next · multi-workspace · swarm)

**The engineering references** (governing build docs; linked from code, gates, and AGENTS.md —
not Mintlify pages):

- `ARCHITECTURE.md` — the constitution (P1–P21+), each principle tied to a CI gate
- `CONTROL-PLANE.md` — the applied meetings⊥agent separation + critical-path catalog
- `adr/` — architecture decision records; `views/` — generated CALM projections (gate:dataflow)
- `PARITY-MAIN.md` (gate:parity) · `ARCH-COMPLIANCE.md` (generated) · `LEARNINGS.md` (the
  incident ledger) · `MATURITY-FINDINGS.md` · `test-scenarios/` · `DOCS-GAPS.md` (docs debt —
  delete when empty) · `RELEASE-PLAN.md` · `PLAN-TEMPLATE.md` · `SESSION-HANDOFF.md`

_Governed by `docs/ARCHITECTURE.md` (P1–P21). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
