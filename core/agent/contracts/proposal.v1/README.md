# proposal.v1 — the human gate for external VCS actions

An agent routine never touches GitHub. It **emits a Proposal** (what it wants done, where, and why);
a **human decides** it; a separate, credentialed **executor** (future PR) consumes the approved feed
and performs only what was approved. The trust argument is *topological*: the credential and the gate
sit on **opposite sides of the agent**. The worker container holds no GitHub token — its only voice is
`POST /internal/proposals`, authenticated by its per-dispatch identity token (the token's `sub` must
equal the proposal's `subject`). The token-holder (the executor) takes input only from the
human-approved stream. A prompt-injected unit can therefore at worst *ask*, never *act*.

## The level ↔ action binding (structural, not data)

| level | actions | approval |
|---|---|---|
| **L2** — annotate | `comment` · `label` · `close` · `open_issue` | batch-approvable (`POST /api/proposals/decide`) |
| **L3** — mutate | `push_branch` · `open_pr` | **per-action ONLY** (`POST /api/proposals/{id}/approve`) |

The binding is enforced three times, deliberately redundant:

1. **In this schema** — the `allOf` on `Proposal` refuses an L2 proposal carrying a mutating action
   (and vice versa), so a mislabeled proposal is non-conformant *as data*.
2. **At emission** — `put()` rejects an action outside its level, and rejects any proposal whose
   `routine.declared_access` is below the action's level (a routine authored L2 cannot emit an
   `open_pr`; a proposal with **no** declared access is rejected too — fail-closed).
3. **At decision** — the batch decide surface refuses any L3 id: an L3 mutation can never ride a
   bulk approve; each one is a deliberate, single human act.

`L1` exists only as a `declared_access` rank (read-only routines — they may not propose at all
above nothing); proposals themselves are only ever L2 or L3.

## Lifecycle

`pending` → (`approved` | `rejected` | `expired`) ; `approved` → (`executed` | `failed`).
Every transition is XADDed to the append-only `proposal:audit` stream; approval additionally XADDs
the full proposal to `proposal:approved` — the executor's consumer-group feed.

**Status: UNSEALED (stub)** — sealed with the proposal-queue capability. `gate:schema` validates the
goldens (`Proposal.pending-comment`, `Proposal.approved-pr`, `Decision.batch-approve`) against
`#/$defs/Proposal` / `#/$defs/Decision`.
