<h1 align="center" style="margin-top: 0.25em; margin-bottom: 0.5em; font-size: 2.5em; font-weight: 700; letter-spacing: -0.02em;">Vexa</h1>

<p align="center" style="font-size: 1.75em; margin-top: 0.5em; margin-bottom: 0.75em; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em;">
  <strong>Open-source meeting bot API &amp; transcription API</strong>
</p>

<p align="center" style="font-size: 1em; color: #a0a0a0; margin-top: 0.5em; margin-bottom: 1.5em; letter-spacing: 0.01em;">
  meeting bots • real-time transcription • interactive bots • MCP server • self-hosted • <strong>shared meeting workspace</strong>
</p>

<p align="center" style="margin: 1.5em 0; font-size: 1em;">
  <strong>Google Meet</strong> &nbsp;•&nbsp; <strong>Microsoft Teams</strong> &nbsp;•&nbsp; <strong>Zoom</strong>
</p>

<p align="center">
  <a href="https://github.com/Vexa-ai/vexa/stargazers"><img src="https://img.shields.io/github/stars/Vexa-ai/vexa?style=flat-square&color=yellow" alt="Stars"/></a>
  &nbsp;
  <img src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square" alt="License Apache-2.0"/>
  &nbsp;
  <a href="https://discord.gg/Ga9duGkVz9"><img src="https://img.shields.io/badge/Discord-join-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Discord"/></a>
  &nbsp;
  <a href="https://docs.core.vexa.ai"><img src="https://img.shields.io/badge/docs-docs.core.vexa.ai-blue?style=flat-square" alt="Docs"/></a>
  &nbsp;
  <a href="https://core.vexa.ai"><img src="https://img.shields.io/badge/live%20demo-core.vexa.ai-brightgreen?style=flat-square" alt="Live demo"/></a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> •
  <a href="#meeting-api--send-bots-get-transcripts">API</a> •
  <a href="#whats-new-in-012--the-shared-meeting-workspace">What's new in 0.12</a> •
  <a href="https://docs.core.vexa.ai">Docs</a> •
  <a href="#status">Status</a> •
  <a href="https://discord.gg/Ga9duGkVz9">Discord</a>
</p>

---

**Vexa** is an open-source, self-hostable **meeting bot API** and **meeting transcription API** for
Google Meet, Microsoft Teams, and Zoom. Alternative to Recall.ai, Otter.ai, and Fireflies.ai —
self-host so meeting data never leaves your infrastructure, or use [vexa.ai](https://vexa.ai) hosted.

Where Vexa goes further: capture is the front door, not the whole product. The same self-hosted stack
turns those meetings into a **shared, git-versioned workspace your agents build on** — a
[live demo](https://core.vexa.ai) of the 0.12 expansion layer. **Capture is the moat; your agents build the knowledge.**

**The precise wedge** (not just "open source"): **Apache-2.0 permissive** (no restrictive/ELv2 clauses,
no copyleft) · a self-hostable **multi-platform bot-API *server*** (not a desktop notetaker) ·
real-time + interactive + **MCP** · and a **knowledge-as-code workspace** on top.

---

**Data sovereignty** — self-host so meeting data never leaves your infrastructure

**Cost** — replace $17–20/seat SaaS with your own infrastructure

**Embed in your product** — multi-tenant meeting bot API with scoped tokens

**AI agents** — MCP server + sandboxed agents that read and write a shared meeting workspace

---

### Capabilities

| | | Status |
| --- | --- | --- |
| **Meeting bot API** | Send a bot to any meeting: auto-join, record, speak, chat. Open-source alternative to [Recall.ai](https://recall.ai). | ✅ live |
| **Meeting transcription API** | Real-time transcripts via REST and WebSocket. Self-hosted alternative to Otter.ai / Fireflies.ai. | ✅ live |
| **Real-time transcription** | Sub-second per-speaker transcripts during the call. 100+ languages via Whisper. | ✅ live |
| **Interactive bots** | Make bots speak (TTS), send/read chat, and set avatar in live meetings. | ✅ live *(voice off by default)* |
| **In-tab extension** | Transcribe a Google Meet you're already in — no bot, no admission. | ✅ live |
| **MCP server** | Meeting tools for Claude, Cursor, Windsurf — agents join calls, read transcripts, speak. | ✅ live |
| **Multi-tenant** | Users, scoped API tokens, isolated workloads. Deploy once, serve your team. | ✅ live |
| **Terminal / dashboard** | Web workbench — meetings, transcripts, agent chat, the shared workspace. | ✅ live |
| **Shared meeting workspace** | A meeting becomes a live shared session over a git-versioned knowledge workspace. | 🟡 shipping (MVP) |
| **Knowledge-as-code agent** | Sandboxed CLI coding-agent loop pointed at the transcript, writing versioned Markdown. | 🟡 shipping (MVP) |
| **Live copilot** | Chat with the meeting + the workspace, live during the call. | 🟡 shipping (MVP) |

Every capability is a separate service. Pick what you need, skip what you don't. Self-host everything or
use [vexa.ai](https://vexa.ai) hosted.

---

## Why self-host meeting transcription?

**For regulated industries** — banks, financial services, healthcare — meeting data can't leave your
infrastructure. Self-hosting Vexa means zero external data transmission and a full audit trail on your
own infra. Air-gappable.

**For cost-conscious teams** — replace per-seat SaaS pricing. A team paying $17–20/seat/mo for meeting
transcription can self-host Vexa and drop that to infrastructure cost.

**For developers** — embed a meeting bot API in your product. Multi-tenant, scoped API tokens, no
per-user infrastructure.

Build a self-hosted Otter/Fireflies replacement, a Recall.ai-style bot API — **or** the thing none of
them offer: a meeting-intelligence workspace you own.

---

## Quickstart

On a fresh Linux machine (Ubuntu 24.04):

```bash
apt-get update && apt-get install -y make git curl
curl -fsSL https://get.docker.com | sh
git clone https://github.com/Vexa-ai/vexa.git && cd vexa
```

Then choose:

| Command | What you get | Best for |
| --- | --- | --- |
| `make lite` | **Single container**, whole 0.12 control plane, process-backed bots | Quick evaluation, small teams |
| `make all` | Full compose stack, each service separate | Development, production |
| `make bot` | Build the meeting bot image from source | Needed before a bot can join a real meeting |

`make lite` is the one-command path — it provisions PostgreSQL + MinIO and runs everything else
(gateway, admin, meeting-api, runtime, agent control plane, dashboard, redis, the X11/audio stack) in
one image. No GPU required; transcription runs via an external API (or your own GPU service).

Both prompt for a transcription token on first run — get one at
[vexa.ai/account](https://vexa.ai/account), or self-host the transcription service with a GPU. Set
`TRANSCRIPTION_SERVICE_URL` / `TRANSCRIPTION_SERVICE_TOKEN` in the repo-root `.env`.

Once up:

- **Terminal / dashboard** → `http://localhost:13000`
- **API gateway** → `http://localhost:18056`

Guides: [Vexa Lite](deploy/lite/README.md) · [Docker Compose](deploy/compose/README.md) ·
[Deployment](https://docs.core.vexa.ai/deployment).

**Prefer to look before you build? → [core.vexa.ai](https://core.vexa.ai)** runs this same stack, hosted.

---

## Meeting API — Send Bots, Get Transcripts

Send a bot, get real-time, per-speaker transcripts with interactive controls (speak, chat).

```bash
# 1. Send a bot to a Google Meet call
curl -X POST "$API_BASE/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}'

# 2. Get transcripts (poll this any time — it grows as the meeting runs)
curl -H "X-API-Key: <API_KEY>" \
  "$API_BASE/transcripts/google_meet/abc-defg-hij"
```

Set `API_BASE` to `http://localhost:18056` (self-hosted) or your hosted endpoint at
[vexa.ai](https://vexa.ai). Works with `google_meet`, `teams`, and `zoom`.

**Poll until the meeting completes** — you don't have to wait on a webhook. Watch the bot's status and
grab the final transcript when it finishes:

```bash
# Poll the meeting's status until the bot leaves, then fetch the full transcript
while :; do
  status=$(curl -s -H "X-API-Key: <API_KEY>" "$API_BASE/bots" \
    | jq -r '.[] | select(.native_meeting_id=="abc-defg-hij") | .status')
  echo "status: $status"
  [ "$status" = "completed" ] || [ -z "$status" ] && break
  sleep 10
done

curl -H "X-API-Key: <API_KEY>" "$API_BASE/transcripts/google_meet/abc-defg-hij"
```

Prefer push? **Webhooks** deliver status changes too — see the
[API guide](https://docs.core.vexa.ai). For real-time WebSocket streaming, see the docs.

---

## What's new in 0.12 — the shared meeting workspace

Capture is the front door. 0.12 is what sits behind it: **a meeting becomes a live shared session over a
git-versioned knowledge workspace.**

1. A host sends a bot to a meeting (the API above).
2. The meeting is **bound to a shared workspace** — a versioned Markdown knowledge base, git the durable
   state and the undo.
3. Participants **join the live session**: a live transcript feed, plus the shared workspace they can
   chat with and contribute to.
4. Every contribution is **git-attributed** — you can see who added what, and roll it back.

This is the multi-person, self-hosted **LLM-wiki from your meetings** — the whitespace every other
LLM-wiki misses (they're single-user). Sandboxed agents work the live transcript into the workspace:
**knowledge as code**, versioned files agents read, write, and commit like a codebase — with every
external or irreversible action gated **propose → approve → apply** (the human stays the gate).

> **Start with the bot/API; it grows into a self-hosted meeting-intelligence workspace you own. Capture
> is the moat; your agents build the knowledge.**

Try it live at [core.vexa.ai](https://core.vexa.ai). The agent/copilot layer is **built and shipping now**
(MVP) — see [Status](#status) for the honest shipped-vs-roadmap line.

---

## Modular — Pick What You Need

Vexa is a toolkit, not a monolith. Every feature works independently. Use one or all — they compose when
you need them to.

| You're building... | What you run | Skip the rest |
| --- | --- | --- |
| **Self-hosted Otter replacement** | meetings + transcription + multi-platform | agent, workspace |
| **Meeting bot API (like Recall.ai)** | meetings + transcription + token-scoping | agent, workspace |
| **AI meeting assistant** | meetings + transcription + MCP + speaking-bot | shared workspace |
| **Owned meeting-intelligence workspace** | the full stack — meetings + agent + shared workspace | — |

You don't pay a complexity tax for features you don't use. Don't need agents? Don't run the agent
service. Services communicate via REST and Redis, not tight coupling.

---

## Vexa vs the alternatives

| | Self-host | Bot API | Real-time | Interactive/MCP | Owned, git-versioned knowledge workspace | License |
| --- | --- | --- | --- | --- | --- | --- |
| **Vexa** | ✅ your infra | ✅ | ✅ | ✅ | ✅ | **Apache-2.0** |
| **Recall.ai** | ❌ | ✅ | ✅ | limited / ❌ | ❌ | closed |
| **Otter.ai** | ❌ | ❌ | limited | ❌ | ❌ | closed |
| **Meetily** | ✅ (desktop app) | ❌ (not a server) | ✅ | ❌ | ❌ | MIT |

Meetily is a great MIT desktop notetaker — but it isn't a bot-API server and has no owned workspace.
Recall/Otter/Fireflies are closed and cloud-only. Vexa is the permissive-OSS, self-hostable
**bot-API *server*** with a knowledge layer on top.

---

## Deploy & sovereignty

**1. Hosted** — get an API key at [vexa.ai/account](https://vexa.ai/account) and start sending bots. No
infrastructure needed. *Ready to integrate.*

**2. Self-host with Vexa transcription** — run Vexa yourself, use vexa.ai for transcription. No GPU
needed. *Control with minimal DevOps* — see [deploy/](./deploy/).

**3. Fully self-host / air-gapped** — run everything, including your own GPU transcription service.
*Meeting data never leaves your infrastructure.* Bring your own inference.

---

## Status

Honest shipped-vs-roadmap (full tracker: [docs/RELEASE-PLAN.md](docs/RELEASE-PLAN.md)):

- **✅ Shipped & proven live** — the runtime (meeting bots as browser workloads), join + admission +
  real-time per-speaker transcription + attribution + speaking, **validated live across Google Meet,
  Zoom, and Teams**; the transcript API (`/bots`, `/transcripts`); recording; the in-tab extension; and
  the terminal/dashboard.
- **🟡 Shipping now (MVP, hardening)** — the **shared meeting workspace** (workspace lifecycle —
  share / un-share / archive — is live; live multi-party git-attributed contribution is pending its
  end-to-end acceptance test), the knowledge-as-code agent and live copilot; the gateway
  (auth · routing · WS fan-out) and identity (per-dispatch signed tokens · audit) are hardening.
- **🔵 Roadmap** — at-rest encryption (bucket/transcript/token), calendar + email integrations, SDKs,
  full owner-checks. Contract-conformance gates + golden fixtures ship in-repo.

Claims here are scoped to **0.12 reality** — nothing marked ✅ that isn't running. "Built, shipping now,"
not "live and polished."

### Repository layout

| Dir | Role |
| --- | --- |
| `core/runtime/` | kernel — spawn/execute workloads + mount the workspace |
| `core/meetings/` | capture — join → capture → transcript |
| `core/agent/` | execution — transcript → governed action |
| `core/identity/` | access · accounts · tokens · audit |
| `core/gateway/` | the edge — auth · routing · WS fan-out |
| `clients/` | terminal workbench · dashboard · extension · SDKs |
| `calm/` | FINOS CALM model — architecture + controls + patterns |
| `deploy/` · `docs/` | deployment topologies · documentation + ADRs |

Architecture deep-dive: [docs.core.vexa.ai/architecture](https://docs.core.vexa.ai) —
modules, dispatch, execution, streaming, governance, identity/trust.

---

## Contributing

We use **GitHub Issues** as the main feedback channel. Look for `good-first-issue` to get started. Join
[Discord](https://discord.gg/Ga9duGkVz9) to discuss ideas.

Licensing is FINOS-gated: new dependencies must be permissive (MIT/BSD/Apache); see
`license-exceptions.json` and the contribution docs.

## License

**Apache-2.0.** Transcription uses faster-whisper / CTranslate2 (MIT); model weights are downloaded at
runtime, not redistributed. No copyleft in the tree.

## Links

[Website](https://vexa.ai) • [Docs](https://docs.core.vexa.ai) • [Live demo](https://core.vexa.ai) •
[Discord](https://discord.gg/Ga9duGkVz9) • [LinkedIn](https://www.linkedin.com/company/vexa-ai/) •
[X (@grankin_d)](https://x.com/grankin_d)
