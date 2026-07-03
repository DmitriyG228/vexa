# services — `agent` domain (deployment units)

Processes with a lifecycle and an address (C4 "container"). Currently:

- **`agent-api/`** — the agent control plane and **one Dispatcher**: normalizes any trigger (chat
  message · scheduled routine · external event · `transcript.v1`) into a `unit.v1` Invocation, runs a
  governed `claude` turn over a mounted `workspace.v1`, and spawns the sandboxed worker via `runtime.v1`.
  Surfaces `/invocations`, `/api/chat` (SSE), `/api/sessions`, routines, event ingress, and `/health`.
- **`vcs-ingress/`** — the untrusted-event ingress: GitHub App webhooks are signature-verified,
  persisted as `ingress.v1` Deliveries to the `vcs:events` stream (persist-first + replay), and
  dispatched to agent-api `/events` as `event.v1` envelopes carrying **opaque refs only** per
  `vcs:subs` subscription — no payload bytes cross the seam, and the service holds no GitHub token.

Each service is a modular monolith internally (hexagonal — core + ports + adapters) and may import
only its own code, `runtime/contracts`, and `meetings/contracts` (the published `transcript.v1`
seam). It may **never** import `meetings/` internals (`meetings ⊥ agent`).
