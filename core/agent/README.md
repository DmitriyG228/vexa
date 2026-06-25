# agent — ③ EXECUTION domain: a trigger → a governed agent turn over a user workspace

The execution kernel of the platform. Every input — a chat message, a scheduled routine, an external
event, a meeting transcript — is normalized into one **`unit.v1` Invocation** and run through the **one
Dispatcher** (`agent-api`): a governed `claude` turn over a mounted **`workspace.v1`** git repo,
spawned via `runtime.v1`, with post-write workspace re-validation. `meetings ⊥ agent` — the agent
consumes `meetings/contracts` (the published seam), never its internals.

## What's delivered (public surface)

- **`contracts/`** — the domain's published vocabulary: `unit.v1` (universal invocation envelope) plus
  `workspace.v1` · `routine.v1` · `task.v1` · `tool.v1` · `event.v1` · `proactive-card.v1`, and the
  sealed-but-retiring `invoke.v1`. See [`contracts/README.md`](contracts/README.md).
- **`services/agent-api/`** — the control plane (FastAPI): `/invocations` (the dispatch sink),
  `/api/chat` (SSE), `/api/sessions`, routines (cron), generic event ingress, the tool mechanism, and
  `/health`. Hexagonal inside (core + ports + adapters). See [`services/README.md`](services/README.md).

## Planned / in development

The contract set is **unsealed** — fields land additively (P16) as routines, events, and the tool
mechanism mature; `invoke.v1` retires as callers move to `unit.v1`. The consumer of these contracts is
`clients/terminal` (the AI-first workbench). Scoped identity, secrets-as-a-class, and brokered tool
credentials are seams now, impl deferred (P15 · ADR-0003).

_Governed by `docs/ARCHITECTURE.md` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
