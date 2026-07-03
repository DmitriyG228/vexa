# agent · control_plane

The agent control plane: the FastAPI app (`api.py`) and orchestration that dispatches work to workers and reconciles routine/meeting lifecycle. Owns request handling, routine bookkeeping, transcription watching, and event relay — distinct from the `worker/` that runs a single agent workload.

Also owns the proposal queue (`proposals.py`) — the `proposal.v1` HUMAN GATE: workers emit proposed
external VCS actions to the token-verified `/internal/proposals` sink, a human decides them
(L2 batch / L3 per-action, structurally enforced at both ends), and every transition lands on the
append-only `proposal:audit` stream, with approvals fed to `proposal:approved` for the future
credentialed executor.

And the event-binding view (`vcs_subscriptions.py`): the workspace-routine reconciler
(`workspace_routines.py`) compiles `on: vcs.*` routines into `vcs:subs` subscription records
(routine.v1 `kind: event` made real) — agent-api is the ONE writer of that hash; the vcs-ingress
service only reads it to fan verified GitHub deliveries out as `event.v1` envelopes to `/events`.
