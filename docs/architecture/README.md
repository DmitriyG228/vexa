# architecture — how the runtime works (Mintlify pages)

The mechanism behind the primitives: `dispatch.mdx` (trigger → unit.v1 → the one Dispatcher),
`execution.mdx` (worker spawn + workspace mount via runtime.v1), `governance.mdx` (the claude-turn
guardrails), `streaming.mdx` (SSE output), `identity-and-trust.mdx` (the trust chain).
Also: `transcript-persistence.mdx` (redis live tail + postgres durable) and `gates.mdx` (the CI
gates, for contributors — dissolved here from the old `docs/how-ci-works.md`).
