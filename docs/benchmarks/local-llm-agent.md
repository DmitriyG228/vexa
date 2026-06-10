# CLI agent on a customer-local LLM — 4-arm benchmark scorecard

**Pack:** `ei-agent` (issue #22, epic #21 `enterprise-intelligence`) · **Date:** 2026-06-10
**Question:** does a CLI coding agent survive a Claude-Code-style tool loop on a
customer-local Mistral stack (LiteLLM gateway, OpenAI-compatible/Anthropic-route models),
and how big is the gap vs Claude?

## Setup

| Arm | CLI | Model | Route |
|---|---|---|---|
| 1 | Claude Code | `claude-opus-4-8` / `claude-sonnet-4-6` | host credentials (baseline/ceiling) |
| 2 | Claude Code | `mistral-small-latest` | containerized Claude Code (own `CLAUDE_CONFIG_DIR`) → local LiteLLM `/v1/messages` → Mistral La Plateforme |
| 3 | Mistral Vibe | `mistral-small-latest` | Mistral API direct |
| 4 | OpenCode | `mistral-small-latest` | Mistral API direct (OpenAI-compatible) |

Secondary model `devstral-small-latest` (the agent-tuned model the customer may add) was
additionally run on arms 2 and 3. Arm 1 was re-baselined on 2026-06-10 on the current
models (`claude-opus-4-8`, `claude-sonnet-4-6`); the original `claude-sonnet-4-5` rows are
retained below, marked "(historical)".

Same task suite, same prompts, default CLI configs, no per-arm tuning. Tasks:
**T1** transcript → schema-compliant per-meeting markdown (schema compliance · content
accuracy · unassisted completion) · **T2** cited Q&A over a multi-meeting workspace
(correctness · citation validity) · **T3** deterministic multi-step tool-loop task plus
loop-fidelity metrics. All fixtures are synthetic (fictional company); the Mistral key is
injected env-only via the `vexa-secrets` seam. Harness:
[`benchmarks/local-llm-agent/`](../../benchmarks/local-llm-agent/README.md) — reproduce any
cell with `ARM=<1-4> MODEL=<id> benchmarks/local-llm-agent/run.sh`; raw per-run results are
committed as JSONL under `benchmarks/local-llm-agent/runs/`.

Score = mean of the task's 0–1 metrics (excluding `unassisted_completion`). "Tokens in"
counts cache reads; cost for Mistral arms is computed from La Plateforme list prices.

## Results

<!-- GENERATED:scorecard:begin (lib/scorecard.py — do not edit by hand) -->
### Per-arm × per-task scores

| Arm | CLI | Model | Task | Score | Detail | Completed | Wall clock | Tokens in/out | Cost (USD) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | claude-code | claude-opus-4-8 | T1 | 1.0 | schema_compliance=1.0, content_accuracy=1.0, unassisted_completion=1.0 | yes | 39.1s | 128267/2197 | 0.1814 |
| 1 | claude-code | claude-opus-4-8 | T2 | 1.0 | correctness=1.0, citation_validity=1.0, unassisted_completion=1.0 | yes | 25.8s | 121789/1238 | 0.1454 |
| 1 | claude-code | claude-opus-4-8 | T3 | 1.0 | task_success=1.0, unassisted_completion=1.0 | yes | 22.3s | 138362/819 | 0.1308 |
| 1 | claude-code | claude-sonnet-4-5 (historical) | T1 | 1.0 | schema_compliance=1.0, content_accuracy=1.0, unassisted_completion=1.0 | yes | 42.0s | 76808/1776 | 0.0797 |
| 1 | claude-code | claude-sonnet-4-5 (historical) | T2 | 1.0 | correctness=1.0, citation_validity=1.0, unassisted_completion=1.0 | yes | 37.8s | 93228/1643 | 0.0807 |
| 1 | claude-code | claude-sonnet-4-5 (historical) | T3 | 1.0 | task_success=1.0, unassisted_completion=1.0 | yes | 30.9s | 70260/1199 | 0.1027 |
| 1 | claude-code | claude-sonnet-4-6 | T1 | 1.0 | schema_compliance=1.0, content_accuracy=1.0, unassisted_completion=1.0 | yes | 32.1s | 93616/1405 | 0.0772 |
| 1 | claude-code | claude-sonnet-4-6 | T2 | 1.0 | correctness=1.0, citation_validity=1.0, unassisted_completion=1.0 | yes | 30.2s | 89180/934 | 0.0652 |
| 1 | claude-code | claude-sonnet-4-6 | T3 | 1.0 | task_success=1.0, unassisted_completion=1.0 | yes | 24.3s | 84557/688 | 0.0553 |
| 2 | claude-code+litellm | devstral-small-latest | T1 | 0.88 | schema_compliance=1.0, content_accuracy=0.75, unassisted_completion=1.0 | yes | 203.3s | 198851/776 | 0.0201 |
| 2 | claude-code+litellm | devstral-small-latest | T2 | 1.0 | correctness=1.0, citation_validity=1.0, unassisted_completion=1.0 | yes | 128.2s | 257720/509 | 0.0259 |
| 2 | claude-code+litellm | devstral-small-latest | T3 | 1.0 | task_success=1.0, unassisted_completion=1.0 | yes | 34.8s | 96064/102 | 0.0096 |
| 2 | claude-code+litellm | mistral-small-latest | T1 | 1.0 | schema_compliance=1.0, content_accuracy=1.0, unassisted_completion=1.0 | yes | 384.7s | 159577/640 | 0.0161 |
| 2 | claude-code+litellm | mistral-small-latest | T2 | 1.0 | correctness=1.0, citation_validity=1.0, unassisted_completion=1.0 | yes | 293.6s | 197552/537 | 0.0199 |
| 2 | claude-code+litellm | mistral-small-latest | T3 | 1.0 | task_success=1.0, unassisted_completion=1.0 | yes | 147.9s | 115814/246 | 0.0117 |
| 3 | vibe | devstral-small-latest | T1 | 0.69 | schema_compliance=1.0, content_accuracy=0.38, unassisted_completion=1.0 | yes | 336.9s | — | — |
| 3 | vibe | devstral-small-latest | T2 | 1.0 | correctness=1.0, citation_validity=1.0, unassisted_completion=1.0 | yes | 91.7s | — | — |
| 3 | vibe | devstral-small-latest | T3 | 1.0 | task_success=1.0, unassisted_completion=1.0 | yes | 63.1s | — | — |
| 3 | vibe | mistral-small-latest | T1 | 1.0 | schema_compliance=1.0, content_accuracy=1.0, unassisted_completion=1.0 | yes | 9.7s | — | — |
| 3 | vibe | mistral-small-latest | T2 | 1.0 | correctness=1.0, citation_validity=1.0, unassisted_completion=1.0 | yes | 7.3s | — | — |
| 3 | vibe | mistral-small-latest | T3 | 1.0 | task_success=1.0, unassisted_completion=1.0 | yes | 5.8s | — | — |
| 4 | opencode | mistral-small-latest | T1 | 0.94 | schema_compliance=1.0, content_accuracy=0.88, unassisted_completion=1.0 | yes | 37.9s | 36712/507 | 0.0058 |
| 4 | opencode | mistral-small-latest | T2 | 1.0 | correctness=1.0, citation_validity=1.0, unassisted_completion=1.0 | yes | 63.4s | 49471/345 | 0.0076 |
| 4 | opencode | mistral-small-latest | T3 | 1.0 | task_success=1.0, unassisted_completion=1.0 | yes | 40.9s | 38587/134 | 0.0059 |

### Tool-loop fidelity (T3 task + loop metrics across all tasks)

| Arm | Model | Task | Turns | Tool calls | Tool errors | Valid-tool-call rate |
|---|---|---|---|---|---|---|
| 1 | claude-opus-4-8 | T1 | 6 | 4 | 0 | 1.0 |
| 1 | claude-opus-4-8 | T2 | 8 | 6 | 0 | 1.0 |
| 1 | claude-opus-4-8 | T3 | 6 | 4 | 0 | 1.0 |
| 1 | claude-sonnet-4-5 (historical) | T1 | 6 | 4 | 0 | 1.0 |
| 1 | claude-sonnet-4-5 (historical) | T2 | 8 | 6 | 0 | 1.0 |
| 1 | claude-sonnet-4-5 (historical) | T3 | 6 | 4 | 0 | 1.0 |
| 1 | claude-sonnet-4-6 | T1 | 6 | 4 | 0 | 1.0 |
| 1 | claude-sonnet-4-6 | T2 | 8 | 6 | 0 | 1.0 |
| 1 | claude-sonnet-4-6 | T3 | 6 | 4 | 0 | 1.0 |
| 2 | devstral-small-latest | T1 | 10 | 9 | 5 | 0.44 |
| 2 | devstral-small-latest | T2 | 13 | 12 | 5 | 0.58 |
| 2 | devstral-small-latest | T3 | 5 | 4 | 0 | 1.0 |
| 2 | mistral-small-latest | T1 | 8 | 7 | 4 | 0.43 |
| 2 | mistral-small-latest | T2 | 10 | 9 | 3 | 0.67 |
| 2 | mistral-small-latest | T3 | 6 | 5 | 1 | 0.8 |
| 3 | devstral-small-latest | T1 | 12 | 11 | 0 | 1.0 |
| 3 | devstral-small-latest | T2 | 5 | 6 | 0 | 1.0 |
| 3 | devstral-small-latest | T3 | 7 | 6 | 0 | 1.0 |
| 3 | mistral-small-latest | T1 | 4 | 4 | 0 | 1.0 |
| 3 | mistral-small-latest | T2 | 4 | 6 | 0 | 1.0 |
| 3 | mistral-small-latest | T3 | 5 | 4 | 0 | 1.0 |
| 4 | mistral-small-latest | T1 | 4 | 4 | 0 | 1.0 |
| 4 | mistral-small-latest | T2 | 6 | 7 | 0 | 1.0 |
| 4 | mistral-small-latest | T3 | 5 | 4 | 0 | 1.0 |
<!-- GENERATED:scorecard:end -->

## Reading & go/no-go

**The chain works.** Arm 2 — containerized Claude Code with its own `CLAUDE_CONFIG_DIR`,
pointed at a local LiteLLM container's Anthropic `/v1/messages` route, backed by
`mistral-small-latest` — completed **all of T1–T3 end-to-end, unassisted, with perfect
task scores**. Switching arms/models touches env/config only (`ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`, the LiteLLM model map); no CLI was forked.

**Quality gap vs Claude: smaller than expected on this suite.** On the meeting-intelligence
tasks (T1 note-writing, T2 cited recall), `mistral-small-latest` matched the Claude baseline
scores in every arm; the only sub-1.0 cell was OpenCode's T1 content accuracy (0.88 —
dropped one reference fact). The gap shows up in *loop fidelity and speed*, not outcomes:

- **Arm 2 is functional but heavy.** 148–385 s per task (≈7–11× the `claude-opus-4-8`
  baseline; ≈6–12× vs `claude-sonnet-4-6`),
  ~150k input tokens per task: Claude Code's large system prompt is resent every turn and
  prompt-caching does not apply through LiteLLM→Mistral, which also triggered La Plateforme
  429 rate-limit retries mid-loop. Tool-error rate was visibly worse than baseline
  (e.g. 4 errored calls in T1) though the model always recovered and converged.
- **The Mistral-native CLIs are the sweet spot.** Vibe (arm 3) ran the whole suite in
  6–10 s per task with a 100% valid-tool-call rate; OpenCode (arm 4) in 23–63 s at
  ~$0.006–0.008 per task. Both completed everything unassisted on first attempt.
- **`devstral-small-latest` (secondary) completed everything too but wrote thinner T1
  notes** (content accuracy 0.75 on arm 2, 0.38 on arm 3 — it summarizes more
  aggressively, dropping reference decisions/action items) while keeping perfect schema,
  Q&A and tool-task scores. For meeting-intelligence writing, `mistral-small-latest`
  beat the agent-tuned model on this suite; devstral was faster in the arm-2 chain.

**Go/no-go for "agent on customer-local Mistral": GO.** A Mistral-Small-class model
survives a Claude-Code-style tool loop and completes real meeting-intelligence work.
Recommended shape for the customer sprint: **Mistral Vibe or OpenCode against the
customer's OpenAI-compatible endpoint as the daily driver**, with the Claude Code →
LiteLLM `/v1/messages` chain validated as the compatibility path where the Claude Code
UX/feature set is required (expect higher latency and per-turn token amplification, and
provision rate limits accordingly — or front it with a caching/vLLM tier).

Caveats: single-run point estimates on a 3-task synthetic suite; `mistral-small-latest`
(API) stands in for the customer's self-hosted Mistral-Small-4-class deployment, so
absolute latency/rate-limit numbers will differ behind their LiteLLM/vLLM; per-CLI prompt
tuning was deliberately out of scope. Arm 1 ran on host `claude` login credentials
(documented as the baseline path; re-run wherever host Claude auth exists).
