# GENERATED from architecture.calm.json — do not edit (pnpm arch:dsl --write)

system meetings  # capture → transcribe → record; owns the raw transcript
  service bot
  service desktop
  service meeting-api
  module buffer
  module capture-codec
  module gmeet-capture
  module gmeet-pipeline
  module join
  module mixed-capture-core
  module mixed-pipeline
  module record-chunker
  module recording
  module remote-browser
  module teams-capture
  module whisper
  module zoom-capture
  contract acts.v1
  contract captured-signal.v1
  contract flagged-issue.v1
  contract invocation.v1
  contract lifecycle.v1
  contract transcript.v1
  contract webhook.v1
  service transcription
  data-asset segments-stream [writers: bot]
  data-asset tc-stream [writers: meeting-api]
  data-asset tc-mutable [writers: bot, meeting-api]
  data-asset bm-status [writers: meeting-api]
  data-asset u-meetings [writers: meeting-api]
  data-asset bot-commands [writers: meeting-api]
  database segments-table [writers: meeting-api]
  data-asset recording-blob [writers: bot, meeting-api]

system agent  # copilot; owns the processed (cleaned) transcript + signals
  service agent-api
  service vcs-ingress
  service vcs-executor
  contract event.v1
  contract ingress.v1
  contract invoke.v1
  contract proactive-card.v1
  contract proposal.v1
  contract routine.v1
  contract task.v1
  contract tool.v1
  contract unit.v1
  contract workspace.v1
  service agent-worker
  data-asset out-stream [writers: agent-worker]
  data-asset unit-in
  data-asset proc-stream [writers: agent-worker]
  data-asset proposal-queue [writers: agent-api]
  data-asset proposal-audit [writers: agent-api]
  data-asset vcs-events [writers: vcs-ingress]
  data-asset vcs-subs [writers: agent-api]
  data-asset va-chat

system gateway-system  # the one public edge (api.v1, ws.v1)
  service conformance
  service gateway
  contract api.v1
  contract logevent.v1
  contract ws.v1

system identity  # access + audit; owns the durable DB
  service admin-api
  contract identity.v1
  data-asset identity-db [writers: admin-api]

system runtime-system  # workload spawn (bot/agent containers)
  contract runtime.v1
  contract schedule.v1
  service runtime

system deploy  # deployment + execution-target registry
  contract execution-targets.v1

system platform  # shared infra backing the services
  service redis
  database postgres
  service minio

system ops  # platform support: help surfaces for the people deploying/operating vexa + the doc-gap telemetry loop — additive by design, no product domain depends on it
  service help-mcp
  data-asset help-questions [writers: help-mcp]

edges:
  bot -write-> segments-stream
  bot -write-> tc-mutable
  meeting-api -read-> segments-stream
  meeting-api -write-> tc-mutable
  meeting-api -write-> segments-table
  meeting-api -write-> tc-stream
  agent-api -read-> tc-stream
  gateway -read-> tc-mutable
  terminal -read-> tc-stream
  terminal -read-> proc-stream
  terminal -read-> out-stream
  bot -write-> recording-blob
  gateway -read-> recording-blob
  bot -call-> transcription  # audio -> first-party STT via TRANSCRIPTION_SERVICE_URL
  bot -read-> bot-commands  # SUBSCRIBE acts.v1 commands
  meeting-api -write-> bm-status  # PUBLISH status
  meeting-api -write-> u-meetings  # PUBLISH per-user status
  meeting-api -write-> bot-commands  # PUBLISH leave/speak
  meeting-api -write-> recording-blob  # S3 PUT stitched master
  meeting-api -write-> postgres
  meeting-api -write-> minio
  meeting-api -req-> runtime  # POST /workloads spawn bot
  agent-api -read-> segments-stream  # XREADGROUP agent_copilot (proactive watcher)
  agent-api -req-> runtime  # POST /workloads spawn agent-worker
  agent-api -read-> out-stream  # SSE relay (/api/chat, /api/meeting/stream)
  agent-worker -read-> tc-stream  # copilot tails transcript
  agent-worker -write-> out-stream  # XADD cards/notes/deltas
  agent-worker -write-> proc-stream  # XADD cleaned 1:1 notes
  agent-worker -read-> unit-in  # chat path XREADs interactive input
  agent-worker -req-> agent-api  # POST /internal/proposals — propose_vcs_action emits proposal.v1, dispatch-token verified (the human-gate seam; no GitHub credential in the worker)
  agent-api -write-> proposal-queue  # HSET/SADD proposals + pending sets (put/decide/mark_executed — the one writer)
  agent-api -write-> proposal-audit  # XADD every status transition + the approved feed the vcs-executor consumes
  vcs-ingress -req-> agent-api  # POST /events — one event.v1 envelope per matching subscription: OPAQUE github:// ref + the routine's plan, never payload bytes
  vcs-ingress -req-> redis  # SET NX delivery-id dedupe + XADD vcs:events (persist-first) + HGET vcs:subs (read-only view)
  vcs-ingress -write-> vcs-events  # XADD one ingress.v1 Delivery per verified webhook BEFORE any dispatch (replay recovers)
  vcs-ingress -read-> vcs-subs  # HGET repo -> subscriptions to fan a delivery out (never writes)
  agent-api -write-> vcs-subs  # HSET/HDEL — the workspace-routine reconciler compiles `on: vcs.*` routines into subscription records (the one writer)
  vcs-executor -read-> proposal-audit  # XREADGROUP proposal:approved (the vcs-executor consumer group, created + owned here) + XACK once the result is reported — never an XADD; agent-api stays the one writer
  vcs-executor -req-> redis  # XREADGROUP/XACK proposal:approved via the owned consumer group (at-least-once: an unreported entry stays pending for redelivery)
  vcs-executor -req-> agent-api  # POST /internal/proposals/{id}/executed — the result report-back (shared-secret bearer, constant-time): agent-api stays the ONE writer of proposal state; 409 = already settled, the idempotency check
  vcs-executor -call-> github  # human-approved proposal.v1 actions only — per-proposal App installation token scoped to the target repo + the proposal's level (L2 annotate; L3 + contents:write); L3 pushes vexa/<proposal-id>-* heads, never a protected branch, never --force
  help-mcp -req-> redis  # XADD help:questions (one telemetry entry per tool call, MAXLEN ~5000 approximate) + XREVRANGE read-back for the maintainer doc-gap routine; redis absence degrades gracefully (logged, the answer still returns)
  help-mcp -write-> help-questions  # the ONE writer of the question-log stream (best-effort telemetry — never fails an answer)
  help-mcp -req-> agent-api  # POST /events — one help.escalated event.v1 envelope per escalation (subject = VEXA_OPS_SUBJECT; source = the filed issue URL or help://draft/<uuid>, an opaque ref — never the question bytes); unreachable agent-api degrades, the escalation answer still returns
  help-mcp -call-> github  # read-only public triage state (open issues labeled 'status: accepted' + 'type: bug', 15-min TTL cache; anonymous by default, optional HELP_GITHUB_TOKEN lifts rate limits) + the ONE config-gated write: filing a structured escalation issue on the public tracker — user-authored question/environment text only, sent at the user's explicit request
  gateway -req-> meeting-api  # proxy /bots /transcripts /meetings /recordings
  gateway -req-> agent-api  # proxy /agent/*
  gateway -req-> admin-api  # POST /internal/validate (authz oracle)
  gateway -read-> bm-status  # WS fan-out
  gateway -read-> u-meetings  # WS auto-subscribe
  gateway -read-> va-chat  # WS fan-out
  admin-api -write-> identity-db
  admin-api -write-> postgres
  terminal -req-> gateway  # all REST via gateway
  terminal -req-> gateway  # live WS via gateway
  slim -req-> gateway  # Python client; REST via gateway
  dashboard -req-> gateway  # dashboard client; live WS via gateway
  extension -req-> gateway  # browser extension client; live WS via gateway
  bot, agent-worker deployed-in runtime
  gateway, meeting-api, agent-api, vcs-ingress, vcs-executor, help-mcp, admin-api, runtime, redis, postgres, minio, transcription deployed-in deploy

flows:
  live-transcript-flow: bot-writes-segments-stream -> collector-reads-segments -> collector-writes-tc -> aw-tcnative -> aw-proc -> terminal-reads-processed
  dispatch-flow: aa-runtime -> workers-deployed -> aw-unitout -> aa-unitout
