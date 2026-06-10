# local-llm-agent benchmark — pack `ei-agent` (issue #22)

Proves a Claude-Code-style CLI agent works against a **customer-local LLM stack**
(LiteLLM gateway → Mistral-family models) and quantifies the gap vs Claude — the 4-arm
matrix from the pack scope. Scorecard: [`docs/benchmarks/local-llm-agent.md`](../../docs/benchmarks/local-llm-agent.md).

| Arm | CLI | Model | Route |
|---|---|---|---|
| 1 | Claude Code | Claude (baseline) | host credentials (`ANTHROPIC_API_KEY` or host `claude` login) |
| 2 | Claude Code | Mistral | containerized Claude Code (own `CLAUDE_CONFIG_DIR`) → local **LiteLLM** container exposing the Anthropic `/v1/messages` route → Mistral La Plateforme |
| 3 | Mistral Vibe | Mistral | direct API, default config |
| 4 | OpenCode | Mistral | direct API (OpenAI-compatible), default config |

Same suite, same prompts, default CLI configs, **no per-arm tuning**. Switching arms or
models touches env/config only — there are no code forks of any CLI.

## One entrypoint

```bash
# the Mistral key arrives via the sanctioned secrets seam — env-only, never on disk:
export MISTRAL_API_KEY="$(cd <vexa-secrets-checkout> && bin/secret no-prod/mistral.enc.env MISTRAL_API_KEY)"

ARM=<1-4> MODEL=<id> benchmarks/local-llm-agent/run.sh        # full T1+T2+T3 suite
ARM=2 MODEL=mistral-small-latest TASKS=t1 benchmarks/local-llm-agent/run.sh
```

Defaults: `MODEL=mistral-small-latest` (arms 2–4), `MODEL=claude-sonnet-4-5` (arm 1),
`TASKS=t1,t2,t3`. Results append to `runs/arm<N>-<model>.jsonl` (one machine-readable
record per arm × model × task: scores, tool-loop metrics, tokens, wall-clock). Regenerate
the scorecard tables with `python3 lib/scorecard.py --write`.

## Task suite

- **T1 workspace-write** — meeting-transcript fixture → per-meeting markdown note
  conforming to the frozen fixture schema (`fixtures/schema/meeting-note.schema.md`;
  the production schema belongs to `ei-workspace`). Scored: schema compliance, content
  accuracy vs reference key facts, unassisted completion.
- **T2 recall Q&A** — questions over a multi-meeting markdown workspace; answers must
  cite source files. Scored: correctness, citation validity.
- **T3 tool-loop fidelity** — deterministic multi-step file task; plus loop metrics
  (valid-tool-call rate, turns, flail) extracted from every arm's machine-readable
  event stream (Claude Code `stream-json`, Vibe `--output json`, OpenCode `--format json`).

## Hard rules (enforced)

- **Synthetic fixtures only** ever cross the external API boundary — every fixture under
  `fixtures/` is fictional (Helios Dynamics), generated for this benchmark. No real
  meeting data.
- **No secrets on disk or in logs.** `MISTRAL_API_KEY` is injected via the environment
  (sanctioned seam above) and forwarded to containers with `docker run -e`. The LiteLLM
  master key is a per-invocation random value for the local proxy only.
- The benchmark stack (`compose.yaml`, project `vexa-bench`) is fully separate from the
  product compose stack; `run.sh` tears the proxy down after arm-2 runs (`KEEP_LITELLM=1`
  to keep it).

## Layout

```
run.sh             single entrypoint (ARM/MODEL/TASKS env)
compose.yaml       LiteLLM proxy (Anthropic /v1/messages route) — arm 2 only
litellm/           proxy model map (key comes from env)
docker/            per-arm agent container recipes (claude-code, vibe, opencode)
prompts/           the three task prompts (shared verbatim by every arm)
fixtures/          synthetic transcripts, frozen note schema, T2 workspace+questions, T3 data
lib/runner.py      orchestrates one arm over the suite, appends JSONL
lib/score.py       deterministic 0–1 scoring per task
lib/scorecard.py   aggregates runs/*.jsonl into the scorecard tables
runs/              committed machine-readable results (JSONL)
```
